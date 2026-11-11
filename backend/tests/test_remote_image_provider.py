from __future__ import annotations

import io
import os
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image, ImageDraw

from schemas.gemini import CatalogImageVerification
from services.image_generation_service import RemoteImageProvider, RemoteNearIdenticalError


def png(image: Image.Image) -> bytes:
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()


def images() -> tuple[bytes, bytes, Image.Image, Image.Image]:
    source = Image.new("RGB", (64, 48), "#dddddd")
    draw = ImageDraw.Draw(source)
    for x in range(0, 64, 8):
        draw.line((x, 0, x, 47), fill="#222222", width=2)
    composite = Image.new("RGB", (64, 48), "#d7c4a1")
    draw = ImageDraw.Draw(composite)
    draw.rectangle((0, 30, 63, 47), fill="#43586a")
    draw.rounded_rectangle((18, 15, 45, 34), radius=4, fill="#24465c")
    mask = Image.new("L", (64, 48), 0)
    ImageDraw.Draw(mask).rounded_rectangle((18, 15, 45, 34), radius=4, fill=255)
    return png(source), png(composite), source, mask


class CompositeStub:
    def __init__(self, composite: bytes, mask: Image.Image):
        self.composite = composite
        self._placed_regions = [{"catalog_item_id": "CHAIR-01", "mask": mask}]
        self.render_metadata = {"pipeline": "fake-composite"}

    def render(self, *args, **kwargs):
        return self.composite

    def locate_items(self, image_bytes, items):
        return CatalogImageVerification(items=[])


@contextmanager
def fake_server(mode: str):
    import httpx

    backend = Path(__file__).resolve().parents[1]
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = os.environ.copy()
    env["FAKE_REMOTE_MODE"] = mode
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "fake_remote_server:app", "--host", "127.0.0.1",
         "--port", str(port), "--log-level", "error"],
        cwd=backend / "tests", env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            if process.poll() is not None:
                raise RuntimeError(f"fake render server exited with {process.returncode}")
            try:
                if httpx.get(url + "/health", timeout=0.2, verify=False).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.05)
        else:
            raise TimeoutError("fake render server did not start")
        yield url
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def render_with(provider: RemoteImageProvider, source: bytes, items: list[dict]):
    return provider.render(
        "blend existing furniture lighting and shadows", source, style="Japandi",
        palette="Warm Earth", items=items, depth_map=None, seed=41,
    )


def test_remote_success_blends_only_dilated_item_regions():
    source_bytes, composite_bytes, _, item_mask = images()
    composite_provider = CompositeStub(composite_bytes, item_mask)
    item = {"catalog_item_id": "CHAIR-01", "title": "Low Chair", "kind": "object",
            "reference_image_bytes": png(item_mask.convert("RGB"))}
    import httpx
    with fake_server("invert") as url, httpx.Client(verify=False) as client:
        provider = RemoteImageProvider(
            composite_provider, http_client=client, remote_url=url + "/render",
            remote_token="test-secret", timeout_seconds=3, blend_margin_pixels=4,
        )
        rendered_bytes = render_with(provider, source_bytes, [item])
    rendered = np.asarray(Image.open(io.BytesIO(rendered_bytes)).convert("RGB"))
    composite = np.asarray(Image.open(io.BytesIO(composite_bytes)).convert("RGB"))
    binary = (np.asarray(item_mask) > 0).astype(np.uint8) * 255
    expanded = cv2.dilate(binary, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))) > 0
    assert np.array_equal(rendered[~expanded], composite[~expanded])
    assert np.any(rendered[expanded] != composite[expanded])
    assert provider.render_metadata["render_quality"] == "remote_rendered"
    assert provider.render_metadata["remote_model_id"] == "fake-local-editor"


def test_timeout_returns_composite_with_quality_flag():
    import httpx

    source_bytes, composite_bytes, _, mask = images()

    class TimeoutClient:
        def post(self, *args, **kwargs):
            raise httpx.ReadTimeout("timed out")

    provider = RemoteImageProvider(
        CompositeStub(composite_bytes, mask), http_client=TimeoutClient(),
        remote_url="http://fake-render/render", remote_token="test-secret", timeout_seconds=0.01,
    )
    result = render_with(provider, source_bytes, [{"catalog_item_id": "CHAIR-01"}])
    assert np.array_equal(
        np.asarray(Image.open(io.BytesIO(result)).convert("RGB")),
        np.asarray(Image.open(io.BytesIO(composite_bytes)).convert("RGB")),
    )
    assert provider.render_metadata["render_quality"] == "composite_fallback"


def test_size_mismatch_returns_composite_with_quality_flag():
    source_bytes, composite_bytes, _, mask = images()
    import httpx
    with fake_server("mismatch") as url, httpx.Client(verify=False) as client:
        provider = RemoteImageProvider(
            CompositeStub(composite_bytes, mask), http_client=client,
            remote_url=url + "/render", remote_token="test-secret",
        )
        result = render_with(provider, source_bytes, [{"catalog_item_id": "CHAIR-01"}])
    assert result == composite_bytes
    assert provider.render_metadata["render_quality"] == "composite_fallback"
    assert "did not match input size" in provider.render_metadata["remote_fallback_reason"]


def test_near_identical_output_fails_instead_of_falling_back():
    source_bytes, composite_bytes, _, mask = images()
    import httpx
    with fake_server("input") as url, httpx.Client(verify=False) as client:
        provider = RemoteImageProvider(
            CompositeStub(composite_bytes, mask), http_client=client,
            remote_url=url + "/render", remote_token="test-secret",
        )
        with pytest.raises(RemoteNearIdenticalError, match="near-identical"):
            render_with(provider, source_bytes, [{"catalog_item_id": "CHAIR-01"}])
    assert provider.render_metadata["render_quality"] == "rejected_near_identical"

