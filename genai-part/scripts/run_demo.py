"""End-to-end demo through the public API, using the settings in .env and the sample
room. Person A stays mocked unless PERSON_A_MODE=live. Writes outputs/plans/demo_report.json."""

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import API_PREFIX, create_app

PREVIEW_TIMEOUT_SECONDS = 600
REFINEMENTS = [
    "Use a darker sofa.",
    "Keep the existing rug.",
    "Make it suitable for a small room.",
    "Keep everything under ₹20,000.",
    "Make the room more minimalist.",
]


def show_budget(budget: dict) -> None:
    print(f"  Total ₹{budget['total_inr']:,.0f} of ₹{budget['budget_inr']:,.0f} "
          f"(partial: {budget['is_partial']})")
    for item in budget["items"]:
        print(f"    {item['slot']:<14} ₹{item['unit_price_inr']:>9,.0f}  {item['name'][:55]}")
    for skipped in budget["skipped_slots"]:
        print(f"    {skipped['slot']:<14} skipped: {skipped['reason']}")


def wait_for_preview(client: TestClient, job_id: str) -> dict:
    deadline = time.monotonic() + PREVIEW_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        state = client.get(f"{API_PREFIX}/previews/{job_id}").json()
        if state["status"] in ("COMPLETED", "FAILED"):
            return state
        time.sleep(1)
    raise TimeoutError("Preview did not finish in time.")


def main() -> None:
    settings = get_settings()
    client = TestClient(create_app(settings))
    report: dict = {"modes": client.get("/health").json()}
    print("Modes:", report["modes"])

    themes = client.get(f"{API_PREFIX}/catalog/themes").json()
    stats = client.get(f"{API_PREFIX}/catalog/stats").json()
    print(f"\n1. Catalog: {stats['total']} products, {stats['with_inr_price']} with INR prices, "
          f"{len(themes)} themes")

    raw_scene = json.loads((ROOT / "data" / "sample_scene.json").read_text(encoding="utf-8"))
    scene = client.post(f"{API_PREFIX}/scene/normalize", json={"raw": raw_scene}).json()
    print(f"\n2. Scene: {scene['room_type']}, {len(scene['objects'])} objects, "
          f"missing: {scene['missing_fields'] or 'nothing'}")

    design = client.post(f"{API_PREFIX}/designs/full", json={
        "theme": "japandi", "budget_inr": 60000, "room_type": "living_room", "scene": scene,
    }).json()
    design_id = design["design_id"]
    print(f"\n3. Design {design_id} (spec by {design['spec']['generated_by']})")
    print(f"  Palette: {', '.join(design['spec']['color_palette'])}")
    show_budget(design["budget"])
    print(f"  Plan: {design['plan']['summary']}")
    for step in design["plan"]["steps"]:
        print(f"    {step['order']}. {step['title']}")

    sample_photo = ROOT / "data" / "samples" / "living_room.jpg"
    with sample_photo.open("rb") as photo:
        upload = client.post(f"{API_PREFIX}/uploads", files={"file": ("living_room.jpg", photo, "image/jpeg")}).json()
    analysis = client.post(f"{API_PREFIX}/uploads/{upload['upload_id']}/analysis").json()
    print(f"\n4. Photo analysis: {[o['label'] for o in analysis['objects']] or analysis['note']}")

    container = client.app.state.container
    if settings.person_a_mode == "mock":
        project = container.person_a.create_project("Demo room", "living_room")
        container.person_a.upload_photos(project.id, [sample_photo] * 5)
        container.person_a.start_reconstruction(project.id)
        job = client.post(f"{API_PREFIX}/designs/{design_id}/preview",
                          json={"project_id": project.id}).json()
        preview = wait_for_preview(client, job["job_id"])
        print(f"  Preview {preview['status']}: {preview['output_urls']}")
        report["preview"] = preview
    else:
        print("  Preview skipped: live mode requires an existing Person A project with a rendered depth map.")

    print("\n5. Refinements")
    report["refinements"] = []
    for message in REFINEMENTS:
        result = client.post(f"{API_PREFIX}/designs/{design_id}/refine", json={"message": message}).json()
        outcome = result["outcome"]
        print(f"  \"{message}\"\n    reran: {outcome['stages_rerun']}; skipped: {outcome['stages_skipped']}; "
              f"slots: {outcome['slots_recomputed'] if outcome['stages_rerun'] else '-'}; "
              f"total ₹{result['budget']['total_inr']:,.0f}; preview stale: {outcome['preview_stale']}")
        report["refinements"].append({"message": message, **outcome})
    show_budget(result["budget"])

    if settings.person_a_mode == "mock":
        options = {"person_a_project_id": project.id}
        edit = client.post(f"{API_PREFIX}/designs/{design_id}/edit-3d", json=options).json()
        job_id = edit["job"]["job_id"]
        client.get(f"{API_PREFIX}/edit-3d/jobs/{job_id}")
        job_state = client.get(f"{API_PREFIX}/edit-3d/jobs/{job_id}").json()
        print(f"\n6. Person A (mock): edit job {job_state['status']}, output {job_state['output_urls']}")
        print(f"  style_prompt: {edit['plan']['request']['style_prompt']}")
        print(f"  Not sent to 3D: {len(edit['plan']['not_sent'])} items (one insert per job, needs an S3 key)")
        report["person_a"] = edit

    final = client.get(f"{API_PREFIX}/designs/{design_id}").json()
    report["design"] = final
    path = Path(settings.outputs_dir) / "plans" / "demo_report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nReport: {path}")


if __name__ == "__main__":
    main()
