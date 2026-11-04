from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import API_PREFIX, create_app

P = API_PREFIX
DESIGN = {"theme": "modern", "budget_inr": 60000, "room_type": "living_room"}


@pytest.fixture
def client(tmp_path):
    settings = Settings(_env_file=None, uploads_dir=tmp_path / "uploads", outputs_dir=tmp_path / "outputs")
    return TestClient(create_app(settings))


def test_health_reports_modes(client):
    assert client.get("/health").json() == {
        "status": "ok", "llm_provider": "offline", "redesign_mode": "mock", "person_a_mode": "mock"}


def test_catalog_endpoints(client):
    themes = client.get(f"{P}/catalog/themes").json()
    assert {t["id"] for t in themes} >= {"japandi", "bohemian"} and len(themes) == 8
    page = client.get(f"{P}/catalog/products", params={"category": "rug", "theme": "bohemian",
                                                      "priced_only": True, "limit": 2}).json()
    assert page["total"] > 0 and len(page["items"]) <= 2
    product_id = page["items"][0]["product_id"]
    assert client.get(f"{P}/catalog/products/{product_id}").json()["category"] == "rug"
    assert client.get(f"{P}/catalog/stats").json()["total"] > 1000


def test_unknown_product_uses_error_envelope(client):
    response = client.get(f"{P}/catalog/products/nope")
    assert response.status_code == 404
    assert response.json()["error"] == "not_found"


def test_invalid_request_uses_error_envelope(client):
    response = client.post(f"{P}/designs", json={"theme": "gothic", "budget_inr": -1})
    assert response.status_code == 422
    body = response.json()
    assert body["error"] == "invalid_request" and len(body["details"]["problems"]) == 2


def test_malformed_design_id_is_rejected(client):
    assert client.get(f"{P}/designs/..%2Fsecret").status_code in (404, 422)
    assert client.get(f"{P}/designs/{'0' * 32}").status_code == 404


def test_stepwise_flow(client):
    created = client.post(f"{P}/designs", json=DESIGN)
    assert created.status_code == 201
    design_id = created.json()["design_id"]

    slots = client.post(f"{P}/designs/{design_id}/recommendations").json()["slots"]
    assert any(s["options"] for s in slots)

    budget = client.post(f"{P}/designs/{design_id}/budget").json()["budget"]
    assert budget["within_budget"] and budget["total_inr"] <= 60000

    tighter = client.post(f"{P}/designs/{design_id}/budget", json={"budget_inr": 10000}).json()["budget"]
    assert tighter["total_inr"] <= 10000

    plan = client.post(f"{P}/designs/{design_id}/plan").json()["plan"]
    assert plan["total_inr"] == tighter["total_inr"]


def test_refine_reports_what_ran(client):
    design_id = client.post(f"{P}/designs/full", json=DESIGN).json()["design_id"]
    result = client.post(f"{P}/designs/{design_id}/refine",
                         json={"message": "Keep everything under ₹20,000."}).json()
    assert result["outcome"]["stages_rerun"] == ["budget", "plan"]
    assert "recommendations" in result["outcome"]["stages_skipped"]
    assert result["budget"]["total_inr"] <= 20000
    assert client.get(f"{P}/designs/{design_id}").json()["revisions"][0]["message"]


def test_preview_flow_in_mock_mode(client):
    design_id = client.post(f"{P}/designs/full", json=DESIGN).json()["design_id"]
    person_a = client.app.state.container.person_a
    project = person_a.create_project("Room", "living_room")
    person_a.upload_photos(project.id, [Path(f"{i}.jpg") for i in range(5)])
    person_a.start_reconstruction(project.id)

    job = client.post(f"{P}/designs/{design_id}/preview", json={"project_id": project.id})
    assert job.status_code == 202
    job_id = job.json()["job_id"]
    assert job.json()["status"] == "QUEUED"
    assert client.get(f"{P}/previews/{job_id}").json()["status"] == "PROCESSING"
    state = client.get(f"{P}/previews/{job_id}").json()
    assert state["status"] == "COMPLETED"
    assert state["task_type"] == "REDESIGN_2D"
    assert state["output_urls"] == [f"mock://{project.id}/outputs/redesign_preview.png"]

    refined = client.post(f"{P}/designs/{design_id}/refine", json={"message": "make it bohemian"})
    assert refined.json()["outcome"]["preview_stale"] is True


def test_preview_requires_a_project_with_a_completed_reconstruction(client):
    design_id = client.post(f"{P}/designs", json=DESIGN).json()["design_id"]
    missing = client.post(f"{P}/designs/{design_id}/preview", json={"project_id": "missing"})
    assert missing.status_code == 404
    assert "Person A" in missing.json()["message"]
    invalid = client.post(f"{P}/designs/{design_id}/preview", json={})
    assert invalid.status_code == 422


def test_upload_rejects_a_disguised_file(client):
    response = client.post(f"{P}/uploads", files={"file": ("room.jpg", b"not an image", "image/jpeg")})
    assert response.status_code == 422


def test_3d_edit_flow_against_mock(client):
    design_id = client.post(f"{P}/designs/full", json=DESIGN).json()["design_id"]
    person_a = client.app.state.container.person_a
    project = person_a.create_project("Room", "living_room")
    person_a.upload_photos(project.id, [Path(f"{i}.jpg") for i in range(5)])
    person_a.start_reconstruction(project.id)

    options = {"person_a_project_id": project.id}
    plan = client.post(f"{P}/designs/{design_id}/edit-3d/plan", json=options).json()
    assert plan["request"]["style_prompt"] and plan["not_sent"]

    job = client.post(f"{P}/designs/{design_id}/edit-3d", json=options)
    assert job.status_code == 202
    job_id = job.json()["job"]["job_id"]
    client.get(f"{P}/edit-3d/jobs/{job_id}")
    assert client.get(f"{P}/edit-3d/jobs/{job_id}").json()["status"] == "COMPLETED"


def test_3d_edit_without_reconstruction_surfaces_upstream_404(client):
    design_id = client.post(f"{P}/designs", json=DESIGN).json()["design_id"]
    person_a = client.app.state.container.person_a
    project = person_a.create_project("Room", None)
    response = client.post(f"{P}/designs/{design_id}/edit-3d", json={"person_a_project_id": project.id})
    assert response.status_code == 404
    assert "reconstruction" in response.json()["message"]


def test_scene_normalize_and_calculate(client):
    scene = client.post(f"{P}/scene/normalize", json={"raw": {"objects": ["sofa"]}}).json()
    assert "dimensions" in scene["missing_fields"]
    products = client.get(f"{P}/catalog/products", params={"priced_only": True, "limit": 1}).json()["items"]
    result = client.post(f"{P}/budget/calculate", json={
        "product_ids": [products[0]["product_id"]], "budget_inr": 100000}).json()
    assert result["total_inr"] == products[0]["price_inr"]


def test_openapi_lists_the_contract(client):
    paths = client.get("/openapi.json").json()["paths"]
    for path in ("/designs", "/designs/full", "/designs/{design_id}/refine", "/designs/{design_id}/preview",
                 "/catalog/themes", "/designs/{design_id}/edit-3d"):
        assert f"{P}{path}" in paths


def test_demo_records_are_off_by_default_and_disclosed_when_on(tmp_path):
    design = {"theme": "japandi", "budget_inr": 60000, "room_type": "bedroom"}
    real = TestClient(create_app(Settings(_env_file=None, uploads_dir=tmp_path / "u", outputs_dir=tmp_path / "o")))
    products = real.get(f"{P}/catalog/products", params={"q": "DEMO"}).json()
    assert products["total"] == 0

    demo = TestClient(create_app(Settings(_env_file=None, demo_catalog=True, uploads_dir=tmp_path / "u",
                                          outputs_dir=tmp_path / "o")))
    budget = demo.post(f"{P}/designs/full", json=design).json()["budget"]
    assert any(i["verification_status"] == "demo" for i in budget["items"])
    assert any("demo records" in note for note in budget["price_notes"])
