from __future__ import annotations

from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # App
    env: str = "dev"
    secret_key: str  # JWT signing secret (HS256)
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 30

    # Database
    database_url: str  # postgresql+asyncpg://...
    database_url_sync: str  # postgresql://... (for alembic)

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Celery
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"

    # MinIO / S3
    s3_endpoint_url: str = "http://localhost:9000"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"  # noqa: S105
    s3_bucket_photos: str = "photos"
    s3_region: str = "us-east-1"

    # Master secret for photo + oauth token encryption (AES-256-GCM)
    master_secret: str  # hex-encoded 32 bytes
    oauth_token_secret: str  # separate secret for oauth token encryption

    # Google OAuth
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8000/v1/integrations/google/callback"

    # Anthropic
    anthropic_api_key: str = ""

    # Gemini (optional — used as LLM fallback when Anthropic fails)
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"

    # CORS
    cors_origins: list[str] = ["http://localhost:3000"]

    # Email (SendGrid)
    sendgrid_api_key: str = ""
    email_from: str = "pd@bdsys.net"

    # Rate limiting
    rate_limit_default: str = "100/minute"
    rate_limit_auth: str = "10/minute"

    @field_validator("master_secret", "oauth_token_secret")
    @classmethod
    def validate_hex_32(cls, v: str) -> str:
        try:
            b = bytes.fromhex(v)
        except ValueError as e:
            raise ValueError("must be hex-encoded") from e
        if len(b) != 32:
            raise ValueError("must be exactly 32 bytes (64 hex chars)")
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # pydantic-settings loads fields from env/.env
