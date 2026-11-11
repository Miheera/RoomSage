from __future__ import annotations

import csv
from decimal import Decimal
from io import BytesIO
import json
import os
from pathlib import Path
import shutil
import sys
from unittest.mock import patch
import time

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))
os.environ.update({
    "DATABASE_URL": f"sqlite:///{ROOT.as_posix()}/out/roomsage_api_audit.sqlite",
    "IMAGE_PROVIDER": "huggingface",
    "HF_HOME": "D:\\huggingface_cache",
    "HF_HUB_OFFLINE": "1",
    "U2NET_HOME": "D:\\huggingface_cache\\rembg",
    "RENDER_OUTPUT_DIR": str(ROOT / "out"),
    "TRACE_PIPELINE": "1",
    "ENVIRONMENT": "development",
})

from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select

from config import settings
settings.environment = "development"
settings.render_output_dir = str(ROOT / "out")
settings.catalog_image_storage_dir = str(BACKEND / "data" / "catalog_reference_images")
settings.hf_home = Path("D:/huggingface_cache")
settings.database_url = f"sqlite:///{ROOT.as_posix()}/out/roomsage_api_audit.sqlite"

from database import SessionLocal, engine
from models import Base, CatalogProduct, Project, Room, RoomPhoto, User
from security import hash_password
from tasks.celery_app import render_celery_app
from tasks.pipeline_tasks import run_catalog_design_task
from services import design_jobs, job_state
from tasks import pipeline_tasks
from routers import designs as designs_router
from services import s3_service


class _MemoryRedis:
    hashes: dict[str, dict[str, str]] = {}
    lists: dict[str, list[str]] = {}

    def pipeline(self): return self
    def hset(self, key, mapping=None, **kwargs): self.hashes.setdefault(key, {}).update({k: str(v) for k, v in (mapping or kwargs).items()}); return self
    def rpush(self, key, value): self.lists.setdefault(key, []).append(str(value)); return self
    def ltrim(self, key, start, end): self.lists[key] = self.lists.get(key, [])[start:None if end == -1 else end + 1]; return self
    def expire(self, *args, **kwargs): return self
    def execute(self): return []
    def hgetall(self, key): return dict(self.hashes.get(key, {}))
    def lrange(self, key, start, end): return self.lists.get(key, [])[start:None if end == -1 else end + 1]
    def close(self): pass


class _MemoryS3:
    objects: dict[str, bytes] = {}
    def get_object(self, *, Bucket, Key): return {"Body": BytesIO(self.objects[Key]), "ContentType": "image/png"}
    def put_object(self, *, Bucket, Key, Body, ContentType=None, **kwargs):
        self.objects[Key] = Body.read() if hasattr(Body, "read") else bytes(Body)
    def upload_fileobj(self, file_obj, bucket, key, ExtraArgs=None): self.objects[key] = file_obj.read()
    def delete_object(self, *, Bucket, Key): self.objects.pop(Key, None)
    def generate_presigned_url(self, operation, Params, ExpiresIn): return f"memory://{Params['Key']}"


def _fake_redis(): return _MemoryRedis()
fake_s3 = _MemoryS3()
job_state.get_redis_client = _fake_redis
design_jobs.get_redis_client = _fake_redis
pipeline_tasks.get_redis_client = _fake_redis
pipeline_tasks.get_s3_client = lambda: fake_s3
designs_router.get_s3_client = lambda: fake_s3
s3_service.get_s3_client = lambda: fake_s3

render_celery_app.conf.update(broker_url="memory://", result_backend="cache+memory://", task_always_eager=False)


def main() -> None:
    engine.dispose()
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    reference_dir = BACKEND / "data" / "catalog_reference_images" / "catalog" / "isolated"
    reference_dir.mkdir(parents=True, exist_ok=True)
    thumbs = BACKEND / "data" / "catalog_image_audit" / "thumbs"
    csv_path = BACKEND / "data" / "furniture_catalog_with_images.csv"
    needed = {"FURN-JAP-001", "CHAIR-JAP-042", "FURN-JAP-004", "LIGHT-JAP-007", "RUG-JAP-006", "DECOR-JAP-008", "PAINT-JAP-087", "FLOOR-JAP-009"}
    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
        products = {row["product_id"]: row for row in csv.DictReader(handle) if row["product_id"] in needed}
    for pid in needed - {"PAINT-JAP-087"}:
        thumb = thumbs / f"{pid}.jpg"
        if not thumb.is_file(): raise RuntimeError(f"Missing existing local catalog thumb: {thumb}")
        key = f"catalog/isolated/{pid}.png"
        with Image.open(thumb) as image:
            image.convert("RGB").save(reference_dir / f"{pid}.png", format="PNG")
        if pid == "FLOOR-JAP-009":
            (reference_dir / f"{pid}.png").write_bytes(thumb.read_bytes())

    with SessionLocal() as db:
        user_id = __import__("uuid").uuid4()
        db.add(User(id=user_id, email="audit@example.test", password_hash=hash_password("Roomsage-Audit-Only-2026")))
        project = Project(id=__import__("uuid").uuid4(), user_id=user_id, name="Local API audit", room_type="living_room", photo_keys=[])
        room = Room(id=__import__("uuid").uuid4(), project_id=project.id, type="living_room", name="Empty sample photo 02", budget=Decimal("150000.00"))
        photo_key = f"{project.id}/rooms/{room.id}/photo_02.jpg"
        db.add_all([project, room, RoomPhoto(room_id=room.id, url=photo_key, order=0)])
        for pid, row in products.items():
            image_key = f"catalog/isolated/{pid}.png"
            db.add(CatalogProduct(
                product_id=pid, title=row["title"], category=row["category"], role=row["role"],
                kind=row["kind"], selectable=row["selectable"].casefold() == "true", image_status="verified",
                theme=row["theme"], room_types=json.loads(row["room_types"]), price_inr=Decimal(row["price_inr"]),
                dimensions_m=json.loads(row["dimensions_m"]), primary_color=row["primary_color"],
                primary_material=row["primary_material"], prompt_phrase=row["prompt_phrase"],
                image_s3_key=image_key, reference_image_s3_key=(image_key if row["kind"] == "object" else None),
                buy_url=row["buy_url"], image_url=None, source_image_url=row["image_url"] or None,
            ))
        db.commit()
    fake_s3.objects[photo_key] = (BACKEND / "tests" / "fixtures" / "sample_room" / "photo_02.jpg").read_bytes()

    from main import app
    print("LOCAL_API_E2E seeded sqlite; entering TestClient", flush=True)
    def run_task_locally(args=None, kwargs=None, task_id=None, **options):
        return run_catalog_design_task.apply(args=args or [], kwargs=kwargs or {}, task_id=task_id)
    with patch.object(run_catalog_design_task, "apply_async", side_effect=run_task_locally), TestClient(app) as client:
        print("LOCAL_API_E2E app startup complete", flush=True)
        registration = client.post("/api/v1/auth/login", json={"email": "audit@example.test", "password": "Roomsage-Audit-Only-2026"})
        print("LOCAL_API_E2E login", registration.status_code, flush=True)
        registration.raise_for_status()
        token = registration.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        room_id = str(room.id)
        created = client.post(f"/api/v1/rooms/{room_id}/designs", json={"style": "Japandi", "palette": "Warm Earth"}, headers=headers)
        print("POST create", created.status_code, created.text)
        created.raise_for_status()
        first_id = created.json()["design_id"]
        def wait_done(design_id: str):
            deadline = time.time() + 240
            while time.time() < deadline:
                response = client.get(f"/api/v1/designs/{design_id}", headers=headers)
                response.raise_for_status()
                design = response.json()
                if design["status"] in {"done", "failed"}: return design
                time.sleep(1)
            raise TimeoutError(f"Design {design_id} did not finish in 240 seconds.")
        first = wait_done(first_id)
        print("GET design", json.dumps(first, indent=2))
        if first["status"] != "done" or not first["image_url"]: raise RuntimeError(f"first render failed: {first.get('error')}")
        image_response = client.get(first["image_url"], headers=headers)
        print("GET image", image_response.status_code, len(image_response.content), image_response.headers.get("content-type"))
        image_response.raise_for_status()
        parent_image_key = None
        with SessionLocal() as db:
            parent = db.get(__import__("models").Design, first_id)
            parent_image_key = parent.image_url
            parent_bytes = fake_s3.objects[parent_image_key]
        regen = client.post(f"/api/v1/designs/{first_id}/render", headers=headers)
        print("POST regenerate", regen.status_code, regen.text)
        regen.raise_for_status()
        child_id = regen.json()["design_id"]
        child = wait_done(child_id)
        with SessionLocal() as db:
            parent = db.get(__import__("models").Design, first_id)
            child_row = db.get(__import__("models").Design, child_id)
            parent_state = {"id": str(parent.id), "parent_id": str(parent.parent_id) if parent.parent_id else None,
                            "seed": parent.seed, "image_url": parent.image_url,
                            "items": [(it.catalog_item_id, float(it.x) if it.x is not None else None, float(it.y) if it.y is not None else None) for it in parent.items]}
            child_state = {"id": str(child_row.id), "parent_id": str(child_row.parent_id), "seed": child_row.seed,
                           "image_url": child_row.image_url,
                           "items": [(it.catalog_item_id, float(it.x) if it.x is not None else None, float(it.y) if it.y is not None else None) for it in child_row.items]}
            parent_unchanged = parent.image_url == parent_image_key and fake_s3.objects[parent_image_key] == parent_bytes
        result = {"first_design": first, "child_design": child, "parent_db": parent_state, "child_db": child_state,
                  "different_seed": child_state["seed"] != parent_state["seed"],
                  "different_placement": parent_state["items"] != child_state["items"],
                  "parent_unchanged": parent_unchanged,
                  "execution":"real API route and task body, local synchronous Celery task.apply; no broker worker"}
        out = ROOT / "out" / "local_api_e2e_result.json"
        out.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print("RESULT", out)
        print(json.dumps(result, indent=2))


if __name__ == "__main__": main()
