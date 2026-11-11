from __future__ import annotations

import csv
import json
import os
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
sys.path.insert(0, str(BACKEND))

# The remote provider still uses the existing local catalog compositor to make
# its image/layout guide. Disable only optional local harmonization for this
# integration check; the notebook response path is what this check exercises.
os.environ.setdefault("HF_HARMONIZE_ENABLED", "false")
os.environ.setdefault("HF_HOME", r"D:\huggingface_cache")
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("U2NET_HOME", r"D:\huggingface_cache\rembg")

from services.image_generation_service import HuggingFaceImageProvider, RemoteImageProvider  # noqa: E402


OBJECT_IDS = ("FURN-JAP-001", "CHAIR-JAP-042", "FURN-JAP-004", "LIGHT-JAP-007", "RUG-JAP-006", "DECOR-JAP-008")


class CachedCompositeProvider:
    def __init__(self) -> None:
        self.provider = HuggingFaceImageProvider()
        self.cached: bytes | None = None

    def render(self, *args, **kwargs) -> bytes:
        if self.cached is None:
            self.cached = self.provider.render(*args, **kwargs)
        return self.cached

    def locate_items(self, *args, **kwargs):
        return self.provider.locate_items(*args, **kwargs)

    @property
    def render_metadata(self):
        return self.provider.render_metadata

    @property
    def _placed_regions(self):
        return self.provider._placed_regions

    @property
    def _trace(self):
        return self.provider._trace


def local_fake_server() -> tuple[subprocess.Popen, str]:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = os.environ.copy()
    env["FAKE_REMOTE_MODE"] = "composite"
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "fake_remote_server:app", "--host", "127.0.0.1",
         "--port", str(port), "--log-level", "error"],
        cwd=BACKEND / "tests", env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        if process.poll() is not None:
            raise RuntimeError(f"fake remote server exited with {process.returncode}")
        try:
            if httpx.get(base + "/health", timeout=0.2, verify=False).status_code == 200:
                return process, base
        except httpx.HTTPError:
            time.sleep(0.05)
    process.terminate()
    raise TimeoutError("fake remote server did not start")


def make_items() -> list[dict]:
    catalog_path = BACKEND / "data" / "furniture_catalog_with_images.csv"
    thumbs = BACKEND / "data" / "catalog_image_audit" / "thumbs"
    with catalog_path.open(encoding="utf-8-sig", newline="") as handle:
        rows = {row["product_id"]: row for row in csv.DictReader(handle)}
    items = []
    for product_id in OBJECT_IDS:
        row = rows[product_id]
        items.append({
            "catalog_item_id": product_id, "title": row["title"], "name": row["title"],
            "kind": "object", "role": row["role"], "qty": 1,
            "dimensions_m": json.loads(row["dimensions_m"]),
            "price_inr": row["price_inr"], "style_match": True,
            "reference_image_bytes": (thumbs / f"{product_id}.jpg").read_bytes(),
        })
    paint = rows["PAINT-JAP-087"]
    floor = rows["FLOOR-JAP-009"]
    items.extend([
        {"catalog_item_id": "PAINT-JAP-087", "title": paint["title"], "name": paint["title"],
         "kind": "surface", "role": "paint", "qty": 1, "primary_color": paint["primary_color"],
         "prompt_phrase": paint["prompt_phrase"], "price_inr": paint["price_inr"], "style_match": True},
        {"catalog_item_id": "FLOOR-JAP-009", "title": floor["title"], "name": floor["title"],
         "kind": "surface", "role": "flooring", "qty": 1, "primary_color": floor["primary_color"],
         "prompt_phrase": floor["prompt_phrase"], "price_inr": floor["price_inr"], "style_match": True,
         "floor_texture_bytes": (thumbs / "FLOOR-JAP-009.jpg").read_bytes()},
    ])
    return items


def main() -> None:
    from PIL import Image

    photo_path = BACKEND / "tests" / "fixtures" / "sample_room" / "photo_02.jpg"
    photo_bytes = photo_path.read_bytes()
    items = make_items()
    design_id = f"remote-photo02-{uuid.uuid4()}"
    context = {
        "design_id": design_id, "room_id": "sample-photo-02", "provider": "remote",
        "model_ids": {"remote_renderer": "fake-local-editor"}, "style": "Japandi",
        "palette": "Warm Earth", "seed": 20261110,
        "selected_items": [{"id": item["catalog_item_id"], "name": item["title"]} for item in items],
    }
    cached_compositor = CachedCompositeProvider()
    process, base = local_fake_server()
    try:
        with httpx.Client(verify=False) as client:
            remote = RemoteImageProvider(
                cached_compositor, http_client=client, remote_url=base + "/render",
                remote_token="test-secret", timeout_seconds=5,
            )
            successful = remote.render(
                "Match room lighting and contact shadows", photo_bytes,
                style="Japandi", palette="Warm Earth", items=items, depth_map=None,
                seed=context["seed"], trace_context=context,
            )
        ok_metadata = remote.render_metadata
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    fallback_provider = RemoteImageProvider(
        cached_compositor, remote_url=base + "/render", remote_token="test-secret",
        timeout_seconds=3,
    )
    fallback = fallback_provider.render(
        "Match room lighting and contact shadows", photo_bytes,
        style="Japandi", palette="Warm Earth", items=items, depth_map=None,
        seed=context["seed"], trace_context={**context, "design_id": design_id + "-fallback"},
    )
    fallback_design = {
        "id": design_id + "-fallback",
        "render_metadata": fallback_provider.render_metadata,
        "render_quality": fallback_provider.render_metadata.get("render_quality"),
    }
    assert ok_metadata["render_quality"] == "remote_rendered", ok_metadata
    assert fallback_design["render_quality"] == "composite_fallback", fallback_design
    out = ROOT / "out"
    out.mkdir(parents=True, exist_ok=True)
    (out / "remote_ok.png").write_bytes(successful)
    (out / "remote_fallback.png").write_bytes(fallback)
    (out / "remote_run_metadata.json").write_text(json.dumps({
        "remote_ok_design": {"id": design_id, "render_metadata": ok_metadata},
        "remote_fallback_design": fallback_design,
    }, indent=2), encoding="utf-8")
    with Image.open(out / "remote_ok.png") as image:
        print(f"remote_ok.png: {image.size}; render_quality={ok_metadata['render_quality']}")
    with Image.open(out / "remote_fallback.png") as image:
        print(f"remote_fallback.png: {image.size}; render_quality={fallback_design['render_metadata']['render_quality']}")


if __name__ == "__main__":
    main()
