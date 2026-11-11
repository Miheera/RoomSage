from __future__ import annotations

import io
import json
import sys
import os
import socket
import subprocess
import time
from pathlib import Path

import httpx
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def image_bytes(image: Image.Image) -> bytes:
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()


def test_render_handler_with_stub_model():
    original = Image.new("RGB", (64, 48), "#dddddd")
    composite = Image.new("RGB", (64, 48), "#cabb9e")
    mask = Image.new("L", (64, 48), 0)
    ImageDraw.Draw(mask).rectangle((15, 12, 45, 38), fill=255)
    item = {"catalog_item_id": "CHAIR-01", "name": "Low chair", "kind": "object",
            "mask_image_field": "item_mask_0"}
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    root = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env.update({"ROOMSAGE_STUB_MODEL": "1", "REMOTE_RENDER_TOKEN": "test-notebook-token"})
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "notebooks.render_server:app", "--host", "127.0.0.1",
         "--port", str(port), "--log-level", "error"],
        cwd=root, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            if process.poll() is not None:
                raise RuntimeError(f"notebook stub server exited with {process.returncode}")
            try:
                if httpx.get(base + "/health", timeout=0.2, verify=False).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.05)
        else:
            raise TimeoutError("notebook stub server did not start")
        with httpx.Client(verify=False) as client:
            denied = client.post(base + "/render", timeout=2)
        assert denied.status_code == 401
        with httpx.Client(verify=False) as client:
            response = client.post(
                base + "/render",
                headers={"Authorization": "Bearer test-notebook-token"},
                data={"style": "Japandi", "palette": "Warm Earth", "seed": "44",
                      "prompt": "match lighting", "items": json.dumps([item])},
                files={
                    "room_photo": ("room.png", image_bytes(original), "image/png"),
                    "composite_image": ("composite.png", image_bytes(composite), "image/png"),
                    "item_mask_0": ("mask.png", image_bytes(mask), "image/png"),
                },
                timeout=10,
            )
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    assert response.status_code == 200, response.text
    assert response.headers["X-Render-Model-Id"] == "stub-room-editor"
    with Image.open(io.BytesIO(response.content)) as result:
        assert result.size == original.size
