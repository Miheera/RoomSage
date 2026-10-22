from functools import lru_cache

from pydantic import field_validator
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
    cors_origins: list[str] = ["http://localhost:5173"]
    s3_bucket: str = "roomsage"
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

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: object) -> object:
        if isinstance(value, str) and not value.lstrip().startswith("["):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
