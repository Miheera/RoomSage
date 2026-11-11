import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "RoomSage API"
    environment: str = "development"
    database_url: str = "postgresql+psycopg://roomsage:roomsage@localhost:5432/roomsage"
    redis_url: str = "redis://localhost:6379/0"
    render_redis_url: str = "redis://localhost:6379/1"
    render_visibility_timeout_seconds: int = Field(default=660, ge=60)
    auth_secret: str = "room-sage-local-development-secret-change-before-deploying"
    gemini_api_key: str = ""
    image_provider: Literal["stub", "huggingface", "remote"] = "stub"
    remote_render_url: str = ""
    remote_render_token: str = ""
    remote_render_timeout_seconds: float = Field(default=180.0, gt=0, le=3600)
    remote_blend_margin_pixels: int = Field(default=12, ge=0, le=128)
    remote_render_model_id: str = "stable-diffusion-v1-5/stable-diffusion-inpainting"
    hf_inpaint_model_id: str = "stable-diffusion-v1-5/stable-diffusion-inpainting"
    hf_base_model_id: str = "stable-diffusion-v1-5/stable-diffusion-v1-5"
    hf_controlnet_depth_model_id: str = "lllyasviel/sd-controlnet-depth"
    hf_segmentation_model_id: str = "openmmlab/upernet-convnext-small"
    hf_furniture_classes: list[str] = Field(default_factory=lambda: [
        "armchair", "bed", "bench", "bookcase", "booth", "buffet", "cabinet",
        "chair", "chest of drawers", "coffee table", "counter", "cushion", "desk",
        "dining table", "fireplace", "lamp", "ottoman", "potted plant", "rug",
        "carpet", "side table", "shelf", "sofa", "stool", "table",
        "television receiver", "wardrobe", "seat", "swivel chair",
    ])
    hf_baseboard_strip_pixels: int = Field(default=12, ge=1, le=64)
    hf_door_protection_dilation_pixels: int = Field(default=24, ge=0, le=32)
    hf_floor_texture_tile_width_fraction: float = Field(default=0.045, gt=0, le=0.5)
    hf_floor_texture_contrast: float = Field(default=0.35, gt=0, le=1)
    hf_legacy_segmentation_model_id: str = "facebook/detr-resnet-50-dc5-panoptic"
    hf_depth_model_id: str = "depth-anything/Depth-Anything-V2-Small-hf"
    hf_inpaint_steps: int = Field(default=10, ge=1, le=50)
    hf_restyle_steps: int = Field(default=12, ge=1, le=50)
    hf_guidance_scale: float = Field(default=5.0, gt=0, le=20)
    hf_max_image_side: int = Field(default=768, ge=256, le=2400)
    hf_legacy_sd_restyle_enabled: bool = False
    hf_phash_max_distance: int = Field(default=4, ge=0, le=64)
    hf_room_width_m: float = Field(default=4.5, gt=1, le=20)
    hf_object_width_fraction: float = Field(default=0.32, gt=0, le=1)
    hf_harmonize_enabled: bool = True
    hf_harmonize_strength: float = Field(default=0.3, gt=0, le=1)
    hf_harmonize_steps: int = Field(default=4, ge=1, le=30)
    hf_harmonize_region_margin_pixels: int = Field(default=24, ge=4, le=128)
    hf_harmonize_min_ssim: float = Field(default=0.6, gt=0, le=1)
    hf_max_placed_objects: int = Field(default=6, ge=1, le=50)
    hf_min_placed_object_pixels: int = Field(default=16, ge=1, le=256)
    hf_support_surface_roles: list[str] = Field(default_factory=lambda: ["table_lamp"])
    hf_stage_timeout_seconds: int = Field(default=600, ge=1)
    catalog_render_timeout_seconds: int = Field(default=600, ge=1)
    render_output_dir: str = "out"
    catalog_image_storage_dir: str = "data/catalog_reference_images"
    meshy_api_key: str = ""
    mock_meshy: bool = False
    meshy_local_cache_dir: str = "data/catalog_3d"
    meshy_poll_interval_seconds: float = 5.0
    meshy_task_timeout_seconds: float = 1800.0
    cors_origins: list[str] = ["http://localhost:5173"]
    s3_bucket: str = Field(
        default="roomsage",
        validation_alias=AliasChoices("S3_BUCKET_NAME", "S3_BUCKET"),
    )
    aws_region: str = "us-east-1"
    aws_access_key_id: str | None = None
    aws_secret_access_key: str | None = None
    s3_endpoint_url: str | None = None
    upload_max_bytes: int = 25 * 1024 * 1024
    s3_presigned_url_expiration: int = 3600
    job_state_ttl_seconds: int = 24 * 60 * 60
    pipeline_estimated_seconds: int = 1800
    task_soft_time_limit_seconds: int = 60 * 60
    task_time_limit_seconds: int = 60 * 60 + 60
    triposr_run_script: str | None = None
    edit_asset_gaussian_count: int = 10_000
    sam_enabled: bool = False
    sam_checkpoint_path: str | None = None
    sam_model_type: str = "vit_b"
    sam_max_keyframes: int = 8
    sam_max_masks_per_keyframe: int = 128
    redesign_steps: int = 20
    redesign_guidance: float = 7.5
    hf_token: str = ""
    use_hf_cloud: bool | None = None
    force_cloud_api: bool = False
    hf_home: str = Field(
        default_factory=lambda: (
            r"D:\huggingface_cache"
            if os.name == "nt"
            else str(Path.home() / ".cache" / "huggingface")
        )
    )
    hf_hub_disable_symlinks_warning: str = "1"

    @property
    def image_model_ids(self) -> dict[str, str]:
        if self.image_provider == "stub":
            return {"image": "stub"}
        if self.image_provider == "remote":
            return {
                "segmentation": self.hf_segmentation_model_id,
                "remote_renderer": self.remote_render_model_id,
            }
        models = {"segmentation": self.hf_segmentation_model_id}
        if self.hf_legacy_sd_restyle_enabled:
            models.update({
                "inpaint": self.hf_inpaint_model_id,
                "base": self.hf_base_model_id,
                "controlnet_depth": self.hf_controlnet_depth_model_id,
                "legacy_segmentation": self.hf_legacy_segmentation_model_id,
                "depth": self.hf_depth_model_id,
            })
        return models

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: object) -> object:
        if isinstance(value, str) and not value.lstrip().startswith("["):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @field_validator("auth_secret")
    @classmethod
    def validate_auth_secret(cls, value: str) -> str:
        if len(value) < 32:
            raise ValueError("AUTH_SECRET must contain at least 32 characters.")
        return value

@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
