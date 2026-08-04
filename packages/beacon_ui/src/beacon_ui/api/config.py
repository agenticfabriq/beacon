"""Beacon API configuration. Reads from env via pydantic-settings."""

from __future__ import annotations

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ApiConfig(BaseSettings):
    database_url: str = Field(
        default="",
        validation_alias=AliasChoices("BEACON_DATABASE_URL", "DATABASE_URL"),
    )
    object_storage: str = "local:///tmp/beacon-objects"
    oidc_issuer: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    oidc_jwks_uri: str = ""
    jwt_signing_key: str = "change-me-in-prod"
    api_key_prefix: str = "bcn_dev"
    dashboard_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("BEACON_API_DASHBOARD_URL", "BEACON_DASHBOARD_URL"),
    )
    cookies_secure: bool = Field(
        default=False,
        validation_alias=AliasChoices("BEACON_API_COOKIES_SECURE", "BEACON_COOKIES_SECURE"),
    )
    # LLM judge (narrative/rubric graders only; never the SQL path). All three
    # must be set for the judge to activate. The endpoint is deployment
    # configuration -- it never appears in code or committed files.
    judge_base_url: str = ""
    judge_api_key: str = ""
    judge_model: str = ""

    model_config = SettingsConfigDict(
        env_prefix="BEACON_",
        case_sensitive=False,
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator("database_url")
    @classmethod
    def _database_url_required(cls, value: str) -> str:
        if not value:
            raise ValueError("database_url is required")
        return value
