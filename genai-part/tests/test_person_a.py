"""Contract tests against the routes and rules in Miheera/RoomSage at commit 23a2fd8.
All HTTP goes through httpx.MockTransport; no test reaches a real service."""

import json
from pathlib import Path

import httpx
import pytest

from app.config import Settings
from app.core.errors import ConflictError, NotFoundError, UpstreamError, ValidationFailed
from app.core.models import Category, Theme
from app.dataset.catalog import Catalog
from app.integration.mapper import Edit3DOptions, Insertion, build_edit_plan
from app.integration.person_a_client import HttpPersonAClient, MockPersonAClient
from app.integration.person_a_schemas import EditJobRequest, JobStatus, RedesignJobRequest
from app.llm import rules
from app.llm.schemas import DesignRequest, Priority
from app.budget.optimizer import optimize_budget
from app.recommendations.engine import RankedProduct, SlotRecommendation
from app.services.design_models import Design
from tests.conftest import make_product

SETTINGS = Settings(_env_file=None, person_a_mode="live", person_a_base_url="http://person-a.test")
PROJECT = {"id": "p1", "name": "Room", "room_type": "living_room", "photo_keys": [],
           "depth_map_s3_key": None, "created_at": "2026-01-01T00:00:00Z"}
JOB = {"job_id": "j1", "project_id": "p1", "task_type": "INSTRUCT_GS2GS_EDIT", "status": "COMPLETED",
       "progress": 100, "estimated_time_remaining_seconds": None, "logs": [], "error_message": None,
       "output_urls": ["https://s3/after_model.splat"], "depth_map_available": False,
       "depth_map_url": None, "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z"}
THEME = {"palette": ["white"], "accent_colors": [], "materials": ["oak"], "prompt": "p",
         "label": "Modern", "description": "d"}


def client_with(handler) -> HttpPersonAClient:
    return HttpPersonAClient(SETTINGS, transport=httpx.MockTransport(handler))


def design_with_items(*products) -> tuple[Design, Catalog]:
    catalog = Catalog(list(products), {"modern": THEME})
    request = DesignRequest(theme=Theme.MODERN, budget_inr=100000)
    design = Design(request=request, spec=rules.build_spec(request, THEME))
    slots = [SlotRecommendation(slot=p.product_id, category=p.category, priority=Priority.RECOMMENDED,
                                options=[RankedProduct(product=p, score=50, reasons=[])]) for p in products]
    design.budget = optimize_budget(slots, 100000)
    return design, catalog


def test_edit_request_matches_person_a_route():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"], seen["body"] = str(request.url), json.loads(request.content)
        return httpx.Response(202, json={"job_id": "j1", "status": "QUEUED", "polling_url": "/api/v1/jobs/j1"})

    result = client_with(handler).start_edit(EditJobRequest(project_id="p1", style_prompt="make it calm"))
    assert seen["url"] == "http://person-a.test/api/v1/jobs/edit"
    assert seen["body"]["style_prompt"] == "make it calm"
    assert "remove_mask_id" not in seen["body"]
    assert result.status is JobStatus.QUEUED


def test_redesign_request_dispatches_to_person_a_job_route():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"], seen["body"] = str(request.url), json.loads(request.content)
        return httpx.Response(
            202,
            json={"job_id": "j1", "status": "QUEUED", "polling_url": "/api/v1/jobs/j1"},
        )

    result = client_with(handler).start_redesign(
        RedesignJobRequest(project_id="p1", prompt="A warm modern living room")
    )
    assert seen["url"] == "http://person-a.test/api/v1/jobs/redesign"
    assert seen["body"] == {"project_id": "p1", "prompt": "A warm modern living room"}
    assert result.status is JobStatus.QUEUED


def test_project_flow_uses_person_a_routes(tmp_path: Path):
    routes = []

    def handler(request: httpx.Request) -> httpx.Response:
        routes.append((request.method, request.url.path))
        if request.url.path.endswith("/reconstruct"):
            return httpx.Response(202, json={"job_id": "j1", "status": "QUEUED"})
        return httpx.Response(200, json=PROJECT)

    photos = [tmp_path / f"{i}.jpg" for i in range(5)]
    for photo in photos:
        photo.write_bytes(b"\xff\xd8\xff")
    client = client_with(handler)
    client.create_project("Room", "living_room")
    client.upload_photos("p1", photos)
    client.start_reconstruction("p1")
    assert routes == [("POST", "/api/v1/projects"), ("POST", "/api/v1/projects/p1/photos"),
                      ("POST", "/api/v1/jobs/reconstruct")]


def test_job_response_parses():
    client = client_with(lambda r: httpx.Response(200, json=JOB))
    assert client.get_job("j1").output_urls == ["https://s3/after_model.splat"]


def test_photo_count_is_checked_before_any_request(tmp_path: Path):
    def handler(request):
        raise AssertionError("no request expected")

    with pytest.raises(ValidationFailed):
        client_with(handler).upload_photos("p1", [tmp_path / "a.jpg"])


@pytest.mark.parametrize(
    "status,expected",
    [(404, NotFoundError), (400, ValidationFailed), (502, UpstreamError), (503, UpstreamError)],
)
def test_upstream_errors_are_mapped(status, expected):
    client = client_with(lambda r: httpx.Response(status, json={"detail": "nope"}))
    with pytest.raises(expected) as caught:
        client.get_job("j1")
    assert "nope" in caught.value.message


def test_connection_failure_is_an_upstream_error():
    def handler(request):
        raise httpx.ConnectError("refused")

    with pytest.raises(UpstreamError):
        client_with(handler).get_job("j1")


def test_mock_mode_is_the_default():
    assert Settings(_env_file=None).person_a_mode == "mock"


def test_mapper_sends_style_and_reports_unsent_items():
    rug = make_product("rug", Category.RUG, 1000)
    lamp = make_product("lamp", Category.LIGHTING, 900)
    design, catalog = design_with_items(rug, lamp)
    plan = build_edit_plan(design, Edit3DOptions(person_a_project_id="p1"), catalog)
    assert plan.request.style_prompt == design.spec.edit_prompt_3d
    assert plan.request.insert_catalog_image_key is None
    assert {n.item for n in plan.not_sent} == {rug.name, lamp.name}


def test_mapper_refuses_product_without_an_s3_key():
    rug = make_product("rug", Category.RUG, 1000)
    design, catalog = design_with_items(rug)
    options = Edit3DOptions(person_a_project_id="p1", insertion=Insertion(
        product_id="rug", target_position=[0, 0, 0.5], target_scale=0.5))
    with pytest.raises(ValidationFailed, match="catalog_image_key"):
        build_edit_plan(design, options, catalog)


def test_mapper_inserts_one_product_with_caller_placement():
    chair = make_product("chair", Category.CHAIR, 1000, catalog_image_key="catalog/chair.png")
    lamp = make_product("lamp", Category.LIGHTING, 900)
    design, catalog = design_with_items(chair, lamp)
    options = Edit3DOptions(person_a_project_id="p1", insertion=Insertion(
        product_id="chair", target_position=[0, 0, 0.5], target_scale=0.5))
    plan = build_edit_plan(design, options, catalog)
    assert plan.request.insert_catalog_image_key == "catalog/chair.png"
    assert plan.request.target_position == [0, 0, 0.5]
    assert [n.item for n in plan.not_sent] == [lamp.name]


@pytest.mark.parametrize("key", ["../secret.png", "/abs/chair.png", "catalog/chair.gif", "a//b.png"])
def test_mapper_rejects_keys_person_a_would_reject(key):
    chair = make_product("chair", Category.CHAIR, 1000, catalog_image_key=key)
    design, catalog = design_with_items(chair)
    options = Edit3DOptions(person_a_project_id="p1", insertion=Insertion(
        product_id="chair", target_position=[0, 0, 0], target_scale=1))
    with pytest.raises(ValidationFailed):
        build_edit_plan(design, options, catalog)


def test_mapper_refuses_an_empty_edit():
    design, catalog = design_with_items(make_product("rug", Category.RUG, 1000))
    with pytest.raises(ConflictError):
        build_edit_plan(design, Edit3DOptions(person_a_project_id="p1", include_style=False), catalog)


def test_mock_enforces_person_a_order(tmp_path: Path):
    mock = MockPersonAClient()
    project = mock.create_project("Room", "living_room")
    with pytest.raises(NotFoundError):
        mock.start_edit(EditJobRequest(project_id=project.id, style_prompt="x"))
    with pytest.raises(ValidationFailed):
        mock.start_reconstruction(project.id)
    mock.upload_photos(project.id, [tmp_path / f"{i}.png" for i in range(5)])
    mock.start_reconstruction(project.id)
    assert project.depth_map_s3_key == f"{project.id}/outputs/depth_map.png"
    redesign = mock.start_redesign(
        RedesignJobRequest(project_id=project.id, prompt="A warm modern living room")
    )
    assert redesign.status is JobStatus.QUEUED
    job = mock.start_edit(EditJobRequest(project_id=project.id, style_prompt="x"))
    assert mock.get_job(job.job_id).status is JobStatus.PROCESSING
    done = mock.get_job(job.job_id)
    assert done.status is JobStatus.COMPLETED and done.output_urls[0].startswith("mock://")
