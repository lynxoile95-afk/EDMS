"""Application configuration (12-factor, env driven)."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---------------- app ----------------
    app_name: str = "EDMS AI"
    environment: str = "development"
    debug: bool = True
    api_v1_prefix: str = "/api/v1"
    public_base_url: str = "http://localhost:8080"
    default_timezone: str = "Asia/Tehran"
    pipeline_version: str = "1.0.0"

    # ---------------- security ----------------
    secret_key: str = "CHANGE_ME_dev_only_secret_key"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 120
    refresh_token_expire_days: int = 7
    auth_provider: Literal["local", "ldap", "oidc"] = "local"
    bootstrap_admin_email: str = "admin@example.com"
    bootstrap_admin_password: str = "Admin123!"

    # ---------------- database ----------------
    database_url: str = "postgresql+psycopg://edms:edms@localhost:5432/edms"
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_echo: bool = False

    # ---------------- redis / celery ----------------
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"
    celery_task_always_eager: bool = False

    # ---------------- object storage ----------------
    storage_backend: Literal["s3", "local"] = "s3"
    local_storage_path: str = "/data/uploads"
    s3_endpoint: str = "http://localhost:9000"
    s3_public_endpoint: str = "http://localhost:9000"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"
    s3_bucket: str = "edms"
    s3_region: str = "us-east-1"
    s3_secure: bool = False
    download_link_ttl_seconds: int = 900

    # ---------------- uploads ----------------
    max_upload_mb: int = 200
    allowed_upload_types: str = "application/pdf"

    # ---------------- antivirus ----------------
    av_enabled: bool = False
    clamav_host: str = "clamav"
    clamav_port: int = 3310

    # ---------------- OCR ----------------
    ocr_enabled: bool = True
    ocr_engine: Literal["tesseract", "paddle"] = "tesseract"
    ocr_langs: str = "fas+eng"
    ocr_dpi: int = 300
    tesseract_cmd: str = "/usr/bin/tesseract"

    # ---------------- VLM fallback ----------------
    vlm_enabled: bool = False
    #: "openai" = OpenAI-compatible server (vLLM / Ollama / LM Studio),
    #: "gemini" = Google Generative Language API.
    vlm_provider: Literal["openai", "gemini"] = "openai"
    vlm_base_url: str = "http://localhost:8000/v1"
    vlm_api_key: str = "EMPTY"
    vlm_model: str = "Qwen/Qwen2.5-VL-7B-Instruct"
    vlm_timeout_seconds: int = 90
    vlm_max_calls_per_document: int = 2
    #: Confidential drawings leave the network unless this is explicitly confirmed.
    #: An admin must acknowledge it through PUT /admin/ai-config; nothing sets it
    #: automatically, not even a .env file alone (see validate_runtime).
    vlm_allow_remote: bool = False

    # ---------------- email / sms ----------------
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_tls: bool = False
    email_from: str = "EDMS <no-reply@example.com>"
    sms_provider: Literal["console", "kavenegar"] = "console"
    sms_api_key: str = ""

    # ---------------- CORS ----------------
    backend_cors_origins: str = "http://localhost:5173,http://localhost:8080,http://localhost"

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.backend_cors_origins.split(",") if o.strip()]

    @property
    def allowed_mime_types(self) -> list[str]:
        return [t.strip() for t in self.allowed_upload_types.split(",") if t.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in {"production", "prod"}

    def validate_runtime(self) -> list[str]:
        """Return a list of unsafe configuration warnings (logged at startup)."""
        warnings: list[str] = []
        if self.secret_key.startswith("CHANGE_ME"):
            warnings.append("SECRET_KEY is still the development default.")
        if self.is_production and self.debug:
            warnings.append("DEBUG=true in production.")
        if self.vlm_enabled and self.vlm_allow_remote:
            warnings.append(
                "VLM_ALLOW_REMOTE=true: the AI fallback will send drawing crops to a public "
                "endpoint. Confidential documents leave the organisation's network."
            )
        elif self.vlm_enabled and "localhost" not in self.vlm_base_url and not self.vlm_base_url.startswith(
            "http://vlm"
        ):
            warnings.append(
                "VLM endpoint is not local. Confidential drawings must be processed on-premise."
            )
        return warnings


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
