import io
from unittest.mock import Mock, patch

import pytest
import httpx
from PIL import Image

from config import settings
from schemas.gemini import ChatRefinementResponse
from services.gemini_service import GeminiService
from services.image_generation_service import (
    HuggingFaceImageProvider,
    ImageGenerationService,
)
from services.redesign_service import RedesignGenerationError, RedesignService


def test_project_photo_upload_openapi_uses_binary_items():
    from main import app

    app.openapi_schema = None
    schema = app.openapi()
    request_schema = schema["paths"]["/api/v1/projects/{project_id}/photos"]["post"][
        "requestBody"
    ]["content"]["multipart/form-data"]["schema"]
    body_name = request_schema["$ref"].rsplit("/", 1)[-1]
    file_schema = schema["components"]["schemas"][body_name]["properties"]["files"]

    assert file_schema["type"] == "array"
    assert file_schema["items"] == {"type": "string", "format": "binary"}
    assert file_schema["description"] == "Upload 1 to 5 room photos"


def test_redesign_depth_map_is_converted_to_rgb():
    depth = Image.new("L", (32, 24), color=128)
    buffer = io.BytesIO()
    depth.save(buffer, format="PNG")

    converted = RedesignService._prepare_control_image(buffer.getvalue())

    assert converted.mode == "RGB"
    assert converted.size == (32, 24)


def test_redesign_control_image_resizes_to_sd15_limit():
    converted = RedesignService._prepare_control_image(Image.new("L", (1024, 768), 128))

    assert converted.mode == "RGB"
    assert converted.size == (512, 384)


def test_redesign_cloud_fallback_returns_image_bytes(monkeypatch):
    monkeypatch.setenv("USE_HF_CLOUD", "true")
    monkeypatch.setenv("HF_TOKEN", "test-token")
    response = Mock(
        status_code=200,
        content=b"png-bytes",
        headers={"content-type": "image/png"},
    )
    response.raise_for_status.return_value = None

    with patch("httpx.post", return_value=response) as post:
        image_bytes = RedesignService().generate_redesign("A modern living room")

    assert image_bytes == b"png-bytes"
    assert post.call_args.kwargs["headers"]["Authorization"] == "Bearer test-token"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("true", True),
        ("1", True),
        ("yes", True),
        ("false", False),
        ("0", False),
        ("no", False),
    ],
)
def test_redesign_use_hf_cloud_parses_boolean_values(monkeypatch, value, expected):
    monkeypatch.setenv("USE_HF_CLOUD", value)

    assert RedesignService().use_hf_cloud is expected


def test_redesign_hf_cloud_failure_falls_back_to_local(monkeypatch):
    monkeypatch.setenv("USE_HF_CLOUD", "yes")
    service = RedesignService()
    service._generate_hf_cloud = Mock(side_effect=RuntimeError("cloud unavailable"))
    service._generate_local = Mock(return_value=b"local-image")

    assert service.generate_redesign("A modern living room") == b"local-image"
    service._generate_hf_cloud.assert_called_once_with("A modern living room")
    service._generate_local.assert_called_once_with("A modern living room", None)


def test_redesign_local_mode_does_not_call_cloud(monkeypatch):
    monkeypatch.setenv("USE_HF_CLOUD", "false")
    service = RedesignService()
    service._generate_local = Mock(return_value=b"local-image")
    service._generate_hf_cloud = Mock(side_effect=AssertionError("cloud must not be called"))

    assert service.generate_redesign("A modern living room") == b"local-image"
    service._generate_local.assert_called_once_with("A modern living room", None)
    service._generate_hf_cloud.assert_not_called()


def test_redesign_cloud_and_local_fail_explicitly(monkeypatch):
    monkeypatch.setenv("USE_HF_CLOUD", "true")
    service = RedesignService()
    service._generate_hf_cloud = Mock(side_effect=RuntimeError("cloud unavailable"))
    service._generate_local = Mock(side_effect=RuntimeError("GPU unavailable"))

    with pytest.raises(RedesignGenerationError, match="cloud unavailable") as exc:
        service.generate_redesign("A modern living room")

    assert "GPU unavailable" in str(exc.value)


def test_redesign_without_gpu_or_hf_token_fails_explicitly(monkeypatch):
    monkeypatch.setenv("USE_HF_CLOUD", "true")
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.setattr(settings, "hf_token", "")

    with pytest.raises(RedesignGenerationError, match="HF_TOKEN"):
        RedesignService().generate_redesign("A modern living room")


def test_gemini_read_timeout_retries_once_with_fallback_model():
    client = Mock()
    client.models.generate_content.side_effect = [
        httpx.ReadTimeout("primary model timed out"),
        Mock(
            parsed=ChatRefinementResponse(
                reply="I will update the room style.",
                refined_prompt="Warm Japandi living room",
            ),
        ),
    ]
    service = GeminiService(client=client)

    result = service.chat_refinement("project-id", "Make it Japandi")

    assert result.refined_prompt == "Warm Japandi living room"
    assert client.models.generate_content.call_count == 2
    assert client.models.generate_content.call_args_list[0].kwargs["model"] == service.MODEL
    assert (
        client.models.generate_content.call_args_list[1].kwargs["model"]
        == service.FALLBACK_MODELS[0]
    )


def test_gemini_read_timeout_does_not_retry_multiple_fallbacks():
    client = Mock()
    client.models.generate_content.side_effect = httpx.ReadTimeout("request timed out")
    service = GeminiService(client=client)

    with pytest.raises(httpx.ReadTimeout, match="request timed out"):
        service.chat_refinement("project-id", "Make it Japandi")

    assert client.models.generate_content.call_count == 2


def test_chat_refinement_timeout_returns_504(monkeypatch):
    from fastapi import HTTPException
    from routers import projects as projects_router
    from routers.projects import ChatRequest

    project_id = "7e047b3b-caca-47e0-8d15-9584bc975a44"
    monkeypatch.setattr(projects_router, "_owned_project", Mock())
    with patch.object(projects_router, "GeminiService") as gemini:
        gemini.return_value.chat_refinement.side_effect = httpx.ReadTimeout(
            "Gemini request timed out"
        )
        with pytest.raises(HTTPException) as exc:
            projects_router.refine_project_design(
                project_id,
                ChatRequest(message="Make it Japandi"),
                Mock(),
                Mock(),
            )

    assert exc.value.status_code == 504
    assert exc.value.detail == "Design refinement timed out. Please try again."


def test_stub_image_provider_returns_png_of_room_photo(monkeypatch):
    monkeypatch.setattr(settings, "image_provider", "stub")
    source = io.BytesIO()
    Image.new("RGB", (80, 60), (30, 120, 180)).save(source, format="JPEG")

    output = ImageGenerationService().render("Japandi living room", source.getvalue())

    with Image.open(io.BytesIO(output)) as image:
        assert image.format == "PNG"
        assert image.size == (80, 60)


def test_huggingface_surface_prompt_omits_catalog_objects():
    prompt = HuggingFaceImageProvider._surface_prompt(
        "Japandi",
        "warm neutrals",
        [
            {"kind": "object", "title": "blue couch", "prompt_phrase": "blue couch"},
            {
                "kind": "surface",
                "role": "paint",
                "primary_color": "warm white",
                "primary_material": "limewash",
                "prompt_phrase": "matte warm-white limewash",
            },
        ],
    )

    assert "Japandi" in prompt
    assert "limewash" in prompt
    assert "couch" not in prompt


def test_huggingface_segmentation_passes_processed_and_target_sizes():
    import contextlib
    import numpy as np
    from PIL import ImageDraw

    segmentation_png = io.BytesIO()
    segmentation_image = Image.new("RGB", (48, 32))
    ImageDraw.Draw(segmentation_image).rectangle((12, 8, 24, 20), fill=(1, 0, 0))
    segmentation_image.save(segmentation_png, format="PNG")

    class Scalar:
        def __init__(self, value):
            self.value = value

        def item(self):
            return self.value

    class Vector:
        def __init__(self, length):
            self.length = length

        def sum(self):
            return Scalar(self.length)

    class Mask:
        def __init__(self, height, width):
            self.height = height
            self.width = width

        def __getitem__(self, index):
            assert index == 0
            return self

        def any(self, dim):
            return Vector(self.height if dim == 1 else self.width)

    class Processor:
        def __call__(self, *, images, return_tensors):
            assert return_tensors == "pt"
            return {"pixel_mask": Mask(16, 24)}

        def post_process_panoptic(self, outputs, *, processed_sizes, target_sizes):
            assert outputs == "model-output"
            assert processed_sizes == [(16, 24)]
            assert target_sizes == [(32, 48)]
            return [
                {
                    "png_string": segmentation_png.getvalue(),
                    "segments_info": [{"id": 1, "category_id": 0}],
                }
            ]

    class Model:
        config = type("Config", (), {"id2label": {0: "couch"}})()

        def __call__(self, **inputs):
            assert "pixel_mask" in inputs
            return "model-output"

    class Torch:
        class cuda:
            @staticmethod
            def empty_cache():
                pass

        @staticmethod
        def inference_mode():
            return contextlib.nullcontext()

    provider = object.__new__(HuggingFaceImageProvider)
    provider._segmenter = (Processor(), Model())
    provider.torch = Torch()

    furniture, floor = provider._segment_room(Image.new("RGB", (48, 32)))

    assert furniture.size == (48, 32)
    assert floor.size == (48, 32)
    assert furniture.getbbox() is not None
    assert provider._segmenter is None


def test_huggingface_output_guard_rejects_near_identical_room(monkeypatch):
    provider = object.__new__(HuggingFaceImageProvider)
    monkeypatch.setattr(settings, "hf_phash_max_distance", 4)
    image = Image.new("RGB", (64, 64), "white")

    with pytest.raises(RuntimeError, match="render did not change the room"):
        provider._check_output_changed(image, image.copy())


def test_huggingface_output_guard_accepts_changed_room(monkeypatch):
    from PIL import ImageDraw

    provider = object.__new__(HuggingFaceImageProvider)
    monkeypatch.setattr(settings, "hf_phash_max_distance", 4)
    source = Image.new("RGB", (64, 64), "white")
    changed = source.copy()
    ImageDraw.Draw(changed).rectangle((4, 4, 58, 58), fill="black")

    provider._check_output_changed(source, changed)


def test_catalog_white_background_is_removed_but_colored_product_is_kept():
    from PIL import ImageDraw

    reference = Image.new("RGB", (100, 100), "white")
    ImageDraw.Draw(reference).rectangle((30, 20, 70, 85), fill=(40, 100, 150))
    buffer = io.BytesIO()
    reference.save(buffer, format="JPEG", quality=100)

    product = HuggingFaceImageProvider._remove_white_background(buffer.getvalue())

    assert product.mode == "RGBA"
    assert product.getchannel("A").getbbox() is not None
    assert product.getpixel((product.width // 2, product.height // 2))[3] > 200


def test_catalog_compositing_tracks_normalized_positions_and_surface_nulls():
    from PIL import ImageDraw

    reference = Image.new("RGB", (100, 100), "white")
    ImageDraw.Draw(reference).rectangle((25, 15, 75, 90), fill=(40, 100, 150))
    buffer = io.BytesIO()
    reference.save(buffer, format="JPEG", quality=100)
    floor = Image.new("L", (256, 256), 0)
    ImageDraw.Draw(floor).polygon([(10, 150), (246, 150), (255, 255), (1, 255)], fill=255)
    provider = object.__new__(HuggingFaceImageProvider)

    rendered, placements, _ = provider._place_catalog_items(
        Image.new("RGB", (256, 256), (220, 210, 195)),
        floor,
        [
            {"catalog_item_id": "CAT-1", "kind": "object", "qty": 1,
             "dimensions_m": [0.8, 0.6, 0.9],
             "reference_image_bytes": buffer.getvalue()},
            {"catalog_item_id": "SURF-1", "kind": "surface"},
        ],
        seed=7,
    )

    object_item = next(item for item in placements.items if item.catalog_item_id == "CAT-1")
    surface_item = next(item for item in placements.items if item.catalog_item_id == "SURF-1")
    assert rendered.size == (256, 256)
    assert object_item.visible
    assert 0 <= object_item.x <= 1
    assert 0 <= object_item.y <= 1
    assert surface_item.visible and surface_item.x is None and surface_item.y is None


def test_legacy_chat_logs_redacted_exception_and_development_class(monkeypatch, caplog):
    from fastapi import HTTPException
    from routers import projects as projects_router
    from routers.projects import ChatRequest

    project_id = "7e047b3b-caca-47e0-8d15-9584bc975a44"
    monkeypatch.setattr(settings, "environment", "development")
    monkeypatch.setattr(settings, "gemini_api_key", "gemini-test-secret")
    monkeypatch.setattr(projects_router, "_owned_project", Mock())
    with (
        caplog.at_level("ERROR", logger="routers.projects"),
        patch.object(projects_router, "GeminiService") as gemini,
    ):
        gemini.return_value.chat_refinement.side_effect = RuntimeError(
            "provider rejected key gemini-test-secret"
        )
        with pytest.raises(HTTPException) as exc:
            projects_router.refine_project_design(
                project_id,
                ChatRequest(message="Make it Japandi"),
                Mock(),
                Mock(),
            )

    assert exc.value.status_code == 502
    assert "RuntimeError" in exc.value.detail
    assert "exception_type=RuntimeError" in caplog.text
    assert "provider rejected key" in caplog.text
    assert "gemini-test-secret" not in caplog.text


def test_provider_startup_log_contains_provider_and_models_but_no_key(monkeypatch, caplog):
    import logging

    from services.image_generation_service import log_image_provider

    monkeypatch.setattr(settings, "image_provider", "huggingface")
    monkeypatch.setattr(settings, "hf_inpaint_model_id", "test/inpaint")
    monkeypatch.setattr(settings, "hf_base_model_id", "test/base")
    monkeypatch.setattr(settings, "hf_controlnet_depth_model_id", "test/controlnet")
    monkeypatch.setattr(settings, "hf_segmentation_model_id", "test/segment")
    monkeypatch.setattr(settings, "hf_depth_model_id", "test/depth")

    with caplog.at_level("INFO", logger="services.image_generation_service"):
        log_image_provider("test")

    assert "provider=huggingface" in caplog.text
    assert "inpaint_model_id=test/inpaint" in caplog.text
    assert "base_model_id=test/base" in caplog.text
    assert "controlnet_model_id=test/controlnet" in caplog.text
    assert "segmentation_model_id=test/segment" in caplog.text
    assert "depth_model_id=test/depth" in caplog.text

    with caplog.at_level("INFO", logger="uvicorn.error"):
        log_image_provider("API", log=logging.getLogger("uvicorn.error"))

    assert "API image configuration" in caplog.text
