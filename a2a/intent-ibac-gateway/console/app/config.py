# Copyright (c) 2026 IndyKite
"""Runtime configuration - one validated Settings object, read once at startup.

Every value comes from the environment (or a .env file). Secrets are SecretStr
so they never land in logs or in /api/config. A blank required value fails the
process at startup instead of a confusing error on the first request.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, HttpUrl, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_APP_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        env_ignore_empty=True,
        str_strip_whitespace=True,
    )

    console_port: int = Field(default=3400, ge=1, le=65535)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # The product under demonstration.
    intent_agent_url: HttpUrl
    intent_agent_health_url: HttpUrl
    intent_agent_audience: str = Field(default="intent_agent", min_length=1)

    # IndyKite Token Service: shown on the Intent Agent fleet page and probed for health.
    token_service_url: HttpUrl
    token_service_client_id: str = Field(min_length=1)
    token_service_client_secret: SecretStr

    # IndyKite platform - vocabulary pages and the decision step.
    indykite_base_url: HttpUrl
    app_agent_credentials_token: SecretStr

    # Audit webhook the Intent Agent and the Token Service deliver to.
    audit_webhook_api_key: SecretStr
    audit_buffer_size: int = Field(default=500, gt=0)

    # The console's login at Auth0: the "Console" Regular Web Application of the tenant, asking for the
    # people's API as audience so the access token is the one the gateways accept.
    auth0_domain: str = Field(min_length=1)
    console_idp_client_id: str = Field(min_length=1)
    console_idp_client_secret: SecretStr
    auth0_people_audience: str = Field(default="ibac-people", min_length=1)
    # Where the browser reaches the console: the callback registered on the Console application.
    # Unset, it is http://localhost:<console_port>; compose sets it to the published host port.
    console_public_url: HttpUrl | None = None
    # The name the console gives itself in the preview's audit records.
    console_client_id: str = Field(default="intent-ibac-console", min_length=1)

    # The fleet's gateways, reached in-network as iag-<agent>:<port>; the ports match docker-compose.yaml.
    gateway_url_template: str = Field(default="http://iag-{agent}:{port}", min_length=1)
    gateway_ports: dict[str, int] = Field(
        default_factory=lambda: {
            "trading-desk": 9901,
            "support-desk": 9902,
            "billing": 9903,
            "people-directory": 9904,
            "workflow-runner": 9905,
            "weather": 9906,
        },
    )

    # Shown on the architecture page only.
    gemini_model: str = ""

    request_timeout_seconds: float = Field(default=30.0, gt=0)
    prompts_file: Path = _APP_DIR / "data" / "prompts.json"
    # The agent cards the IBAC engine matches every intent against.
    fleet_file: Path = _APP_DIR / "data" / "fleet.json"
    static_dir: Path = _APP_DIR / "static"

    @model_validator(mode="after")
    def _public_url_follows_the_port(self) -> Settings:
        if self.console_public_url is None:
            self.console_public_url = HttpUrl(f"http://localhost:{self.console_port}")
        return self

    @field_validator(
        "token_service_client_secret",
        "app_agent_credentials_token",
        "audit_webhook_api_key",
        "console_idp_client_secret",
    )
    @classmethod
    def _not_blank(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            msg = "must not be blank"
            raise ValueError(msg)
        return value
