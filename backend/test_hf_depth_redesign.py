"""Standalone verification suite for the 2D redesign pipeline.

By default, cloud responses and S3 are mocked so the suite is safe and
repeatable. Pass --live-hf or --live-local to generate and save real model output.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from io import BytesIO
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import urlparse
import uuid

import httpx
from PIL import Image, ImageDraw

from config import settings
from services.redesign_service import (
    HF_INFERENCE_URL,
    RedesignGenerationError,
    RedesignService,
)

PROMPT = "Japandi living room with warm lighting"
BACKEND_DIR = Path(__file__).resolve().parent


def _png_bytes(image: Image.Image | None = None) -> bytes:
    output = BytesIO()
    (image or Image.new("RGB", (64, 64), (225, 215, 195))).save(output, format="PNG")
    return output.getvalue()


def _assert_valid_png(image_bytes: bytes) -> None:
    with Image.open(BytesIO(image_bytes)) as image:
        image.verify()
        if image.format != "PNG":
            raise AssertionError(f"Expected PNG output, received {image.format!r}.")


def _save_real_png(image_bytes: bytes, filename: str) -> Path:
    _assert_valid_png(image_bytes)
    if len(image_bytes) <= 100_000:
        raise AssertionError(f"Real render is too small ({len(image_bytes)} bytes).")
    with Image.open(BytesIO(image_bytes)) as image:
        if len(image.convert("RGB").getcolors(maxcolors=1_000_000) or []) < 32:
            raise AssertionError("Real render does not contain enough distinct colors.")
    destination = BACKEND_DIR / filename
    destination.write_bytes(image_bytes)
    return destination


def _synthetic_depth_map() -> Image.Image:
    depth = Image.new("L", (1024, 1024), 32)
    draw = ImageDraw.Draw(depth)
    draw.polygon(((0, 0), (1024, 0), (800, 650), (224, 650)), fill=160)
    draw.polygon(((0, 1024), (1024, 1024), (800, 650), (224, 650)), fill=235)
    draw.rectangle((356, 150, 670, 650), fill=100)
    draw.rectangle((160, 220, 310, 630), fill=80)
    return depth


def _response_for_png(image_bytes: bytes) -> httpx.Response:
    return httpx.Response(
        200,
        headers={"content-type": "image/png"},
        content=image_bytes,
        request=httpx.Request("POST", HF_INFERENCE_URL),
    )


def test_cloud_fallback(live_hf: bool) -> None:
    os.environ["USE_HF_CLOUD"] = "true"
    if live_hf:
        token = os.getenv("HF_TOKEN", settings.hf_token).strip()
        if not token:
            raise RuntimeError("--live-hf requires a configured HF_TOKEN.")
        output = RedesignService().generate_redesign(PROMPT)
    else:
        generated = _png_bytes(Image.new("RGB", (64, 64), (225, 215, 195)))
        response = _response_for_png(generated)
        with patch.dict(os.environ, {"HF_TOKEN": "verification-test-token"}):
            with patch("services.redesign_service.httpx.post", return_value=response) as post:
                output = RedesignService().generate_redesign(PROMPT)
        assert post.call_args.kwargs["json"] == {
            "inputs": PROMPT,
            "options": {"wait_for_model": True},
        }
        assert post.call_args.kwargs["headers"]["Authorization"].startswith("Bearer ")

    _assert_valid_png(output)
    if live_hf:
        destination = _save_real_png(output, "test_hf_cloud_output.png")
        print(f"PASS live cloud inference: render saved to {destination.name}")
    else:
        print("PASS cloud request contract (mocked; no image artifact written).")


def test_depth_map_conditioning(live_local: bool) -> None:
    os.environ["USE_HF_CLOUD"] = "false"
    service = RedesignService()
    depth_map = _synthetic_depth_map()
    if live_local:
        if not service.local_gpu_active:
            raise RuntimeError("--live-local requires an available CUDA GPU.")
        output = service.generate_redesign(PROMPT, depth_map)
    else:
        rendered = Image.new("RGB", depth_map.size, (212, 202, 182))
        draw = ImageDraw.Draw(rendered)
        draw.rectangle((0, 0, 511, 170), fill=(194, 180, 155))
        draw.rectangle((0, 171, 511, 383), fill=(135, 105, 78))
        captured: dict[str, object] = {}

        class FakeControlNetPipeline:
            def __call__(self, **kwargs: object) -> SimpleNamespace:
                captured.update(kwargs)
                return SimpleNamespace(images=[rendered])

        service.local_gpu_active = True
        service._get_controlnet_pipeline = Mock(return_value=FakeControlNetPipeline())
        output = service.generate_redesign(PROMPT, depth_map)
        conditioning_image = captured.get("image")
        assert isinstance(conditioning_image, Image.Image)
        assert conditioning_image.mode == "RGB"
        assert conditioning_image.size == (512, 512)
        assert captured.get("width") == 512
        assert captured.get("height") == 512
        assert captured.get("controlnet_conditioning_scale") == 0.8
        print("INFO depth test used a mocked ControlNet pipeline (no image artifact written).")

    _assert_valid_png(output)
    if live_local:
        with Image.open(BytesIO(output)) as image:
            assert image.size == (512, 512)
        destination = _save_real_png(output, "test_depth_redesign_output.png")
        print(f"PASS live depth-conditioned inference: render saved to {destination.name}")
    else:
        print("PASS depth input contract (mocked; no image artifact written).")


def test_error_boundaries() -> None:
    previous_token = settings.hf_token
    settings.hf_token = ""
    os.environ["HF_TOKEN"] = ""
    os.environ["USE_HF_CLOUD"] = "true"
    try:
        try:
            RedesignService().generate_redesign(PROMPT)
        except RedesignGenerationError as exc:
            assert "HF_TOKEN" in str(exc)
        else:
            raise AssertionError("Missing HF_TOKEN did not raise RedesignGenerationError.")

        os.environ["HF_TOKEN"] = "invalid-test-token"
        payment_required = httpx.Response(
            402,
            text="Payment Required: insufficient inference credits",
            request=httpx.Request("POST", HF_INFERENCE_URL),
        )
        with patch("services.redesign_service.httpx.post", return_value=payment_required):
            try:
                RedesignService().generate_redesign(PROMPT)
            except RuntimeError as exc:
                assert "Hugging Face Cloud API requires paid credits (HTTP 402)" in str(exc)
            else:
                raise AssertionError("An HF 402 response did not raise RuntimeError.")
    finally:
        settings.hf_token = previous_token
        os.environ.pop("HF_TOKEN", None)
    print("PASS error boundaries: missing token and HF 402 errors are explicit.")


@contextmanager
def _session_context(project: SimpleNamespace):
    database = Mock()
    database.get.return_value = project

    class SessionContext:
        def __enter__(self) -> Mock:
            return database

        def __exit__(self, *_: object) -> None:
            return None

    yield SessionContext()


def test_chat_task_s3_integration() -> None:
    from routers import projects as projects_router
    from tasks import pipeline_tasks
    from routers.projects import ChatRequest

    project_id = uuid.uuid4()
    project = SimpleNamespace(
        id=project_id,
        depth_map_s3_key=None,
        s3_redesign_preview_key=None,
        photo_keys=[],
    )
    uploaded: list[dict[str, object]] = []
    generated_png = _png_bytes()
    s3 = Mock()
    s3.put_object.side_effect = lambda **kwargs: uploaded.append(kwargs)
    refinement = SimpleNamespace(reply="Updated the room style.", refined_prompt=PROMPT)

    class FakeRedesignService:
        def generate_redesign(self, prompt: str, depth_map: bytes | None) -> bytes:
            assert prompt == PROMPT
            assert depth_map is None
            return generated_png

    def enqueue_task(*, args: list[str]) -> SimpleNamespace:
        with _session_context(project) as session_context:
            with (
                patch("tasks.pipeline_tasks.SessionLocal", return_value=session_context),
                patch("tasks.pipeline_tasks.get_s3_client", return_value=s3),
                patch("services.redesign_service.RedesignService", FakeRedesignService),
            ):
                key = pipeline_tasks.generate_redesign_preview_task.run(*args)
        return SimpleNamespace(get=lambda **_: key)

    with (
        patch.object(projects_router, "_owned_project", return_value=project),
        patch.object(projects_router, "GeminiService") as gemini,
        patch.object(
            projects_router.generate_redesign_preview_task,
            "apply_async",
            side_effect=enqueue_task,
        ) as enqueue,
        patch.object(
            projects_router,
            "create_presigned_url",
            return_value="https://minio.example.test/preview.png?signature=mock",
        ),
    ):
        gemini.return_value.chat_refinement.return_value = refinement
        response = projects_router.refine_project_design(
            project_id,
            ChatRequest(message="Make the room Japandi"),
            Mock(),
            Mock(),
        )

    assert response["project_id"] == str(project_id)
    assert response["reply"] == refinement.reply
    assert response["refined_prompt"] == PROMPT
    preview_url = response["preview_image_url"]
    assert isinstance(preview_url, str) and urlparse(preview_url).scheme == "https"
    enqueue.assert_called_once_with(args=[str(project_id), PROMPT])
    assert project.s3_redesign_preview_key == uploaded[0]["Key"]
    assert uploaded[0]["Key"].startswith(f"projects/{project_id}/previews/")
    assert uploaded[0]["Key"].endswith(".png")
    assert uploaded[0]["Body"] == generated_png
    assert uploaded[0]["ContentType"] == "image/png"
    print("PASS chat integration: task enqueued, PNG persisted, and preview URL returned.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live-hf",
        action="store_true",
        help="Send a real authenticated image-generation request to Hugging Face.",
    )
    parser.add_argument(
        "--live-local",
        action="store_true",
        help="Run the real local SD 1.5 ControlNet pipeline (requires CUDA and model downloads).",
    )
    args = parser.parse_args()

    tests = (
        ("cloud fallback", lambda: test_cloud_fallback(args.live_hf)),
        ("depth conditioning", lambda: test_depth_map_conditioning(args.live_local)),
        ("error boundaries", test_error_boundaries),
        ("chat/S3 integration", test_chat_task_s3_integration),
    )
    failures: list[str] = []
    for name, test in tests:
        try:
            test()
        except Exception as exc:
            failures.append(name)
            print(f"FAIL {name}: {exc}")

    if failures:
        raise SystemExit(f"Verification failed: {', '.join(failures)}")
    print("Verification suite completed successfully.")


if __name__ == "__main__":
    main()
