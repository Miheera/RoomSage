from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "RoomSage Design Engine"
    cors_origins: list[str] = ["http://localhost:5173"]
    max_upload_bytes: int = 25 * 1024 * 1024

    data_dir: Path = ROOT_DIR / "data"
    uploads_dir: Path = ROOT_DIR / "uploads"
    outputs_dir: Path = ROOT_DIR / "outputs"
    # Demo records stay out of the catalog unless explicitly enabled.
    demo_catalog: bool = False

    # offline: rule-based, no network. anthropic / openai_compatible: real LLM.
    llm_provider: Literal["offline", "anthropic", "openai_compatible"] = "offline"
    llm_model: str = "claude-opus-5-5"
    llm_api_key: str | None = None
    llm_base_url: str | None = None
    llm_timeout_seconds: float = 60.0
    llm_max_retries: int = 2

    # mock: PIL preview that needs no weights. diffusion: real SD pipeline.
    redesign_mode: Literal["mock", "diffusion"] = "mock"
    redesign_device: str = "cuda"
    redesign_max_side: int = 512
    redesign_steps: int = 25
    redesign_guidance: float = 7.5
    redesign_controlnet_scale: float = 0.9
    redesign_seed: int = 42
    inpaint_model: str = "stable-diffusion-v1-5/stable-diffusion-inpainting"
    controlnet_model: str = "lllyasviel/control_v11f1p_sd15_depth"
    depth_model: str = "depth-anything/Depth-Anything-V2-Small-hf"
    segmentation_model: str = "openmmlab/upernet-convnext-tiny"

    # mock unless explicitly switched. Live calls are never the default.
    person_a_mode: Literal["mock", "live"] = "mock"
    person_a_base_url: str = "http://localhost:8000"
    person_a_timeout_seconds: float = 30.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
