# Copyright (c) 2026 IndyKite
"""Runtime configuration - one validated Settings object, read once at startup.

Every value comes from the environment (or a .env file). Secrets are SecretStr
so they never land in logs or in /api/config. A blank required value fails the
process at startup instead of a confusing error on the first request.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, FilePath, HttpUrl, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_APP_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        env_ignore_empty=True,
        str_strip_whitespace=True,
    )

    console_port: int = Field(default=3200, ge=1, le=65535)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # The product under demonstration.
    intent_agent_url: HttpUrl
    intent_agent_health_url: HttpUrl
    intent_agent_audience: str = Field(default="intent_agent", min_length=1)

    # IndyKite Token Service: exchange (console) and introspection (agent) share one client.
    token_service_url: HttpUrl
    token_service_client_id: str = Field(min_length=1)
    token_service_client_secret: SecretStr

    # IndyKite platform - vocabulary pages and the decision step.
    indykite_base_url: HttpUrl
    app_agent_credentials_token: SecretStr

    # Audit webhook the Intent Agent and the Token Service deliver to.
    audit_webhook_api_key: SecretStr
    audit_buffer_size: int = Field(default=500, gt=0)

    # Demo issuer: persona (subject) and console (actor) JWTs the Token Service exchanges.
    demo_idp_signing_key_file: FilePath
    demo_idp_issuer: str = Field(default="https://demo-idp.intent-demo.local", min_length=1)
    demo_idp_subject_audience: str = Field(default="intent-demo-console", min_length=1)
    console_client_id: str = Field(default="intent-demo-console", min_length=1)
    token_ttl_seconds: int = Field(default=300, gt=0)

    # Shown on the architecture page only.
    gemini_model: str = ""

    request_timeout_seconds: float = Field(default=30.0, gt=0)
    prompts_file: Path = _APP_DIR / "data" / "prompts.json"
    static_dir: Path = _APP_DIR / "static"

    @field_validator("token_service_client_secret", "app_agent_credentials_token", "audit_webhook_api_key")
    @classmethod
    def _not_blank(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            msg = "must not be blank"
            raise ValueError(msg)
        return value
