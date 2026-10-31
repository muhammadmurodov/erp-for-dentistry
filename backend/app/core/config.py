"""Environment-driven application settings.

Secrets are read from the environment only. Nothing in this module carries a
usable production default: `SECRET_KEY` and the database password have to be
supplied explicitly, and `validate_for_production` refuses to start a
production process that was handed a development placeholder.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import PostgresDsn, computed_field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["development", "test", "production"]

# Placeholders shipped in .env.example. Refused outside development.
INSECURE_SECRETS = {
    "generate-a-64-byte-random-value-do-not-use-this-one",
    "change-me-in-your-local-env",
    "secret",
    "changeme",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: Environment = "development"
    app_debug: bool = False
    app_name: str = "Business ERP"
    api_prefix: str = "/api/v1"

    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "json"

    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "erp"
    postgres_user: str = "erp"
    postgres_password: str = ""

    redis_url: str = "redis://localhost:6379/0"

    secret_key: str = ""
    access_token_ttl_minutes: int = 15
    refresh_token_ttl_days: int = 14
    password_min_length: int = 12
    login_max_failed_attempts: int = 8
    login_lockout_minutes: int = 15

    cors_origins: str = "http://localhost:5173"

    rate_limit_auth_per_minute: int = 10
    rate_limit_default_per_minute: int = 240

    # Monetary defaults. Currency-specific scale lives in the `currency` table;
    # this is only the fallback used before an organization is resolved.
    default_currency: str = "UZS"

    @field_validator("log_level")
    @classmethod
    def _upper(cls, value: str) -> str:
        return value.upper()

    @computed_field  # type: ignore[prop-decorator]
    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def database_url(self) -> str:
        """Async SQLAlchemy URL (asyncpg driver) used by the application."""
        return str(
            PostgresDsn.build(
                scheme="postgresql+asyncpg",
                username=self.postgres_user,
                password=self.postgres_password,
                host=self.postgres_host,
                port=self.postgres_port,
                path=self.postgres_db,
            )
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def sync_database_url(self) -> str:
        """Synchronous URL. Alembic runs migrations through this one."""
        return str(
            PostgresDsn.build(
                scheme="postgresql+psycopg",
                username=self.postgres_user,
                password=self.postgres_password,
                host=self.postgres_host,
                port=self.postgres_port,
                path=self.postgres_db,
            )
        )

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    def validate_for_production(self) -> None:
        """Fail fast when a production process was started with weak secrets."""
        if not self.is_production:
            return
        problems: list[str] = []
        if not self.secret_key or self.secret_key in INSECURE_SECRETS:
            problems.append("SECRET_KEY is missing or is a known placeholder")
        if len(self.secret_key) < 32:
            problems.append("SECRET_KEY must be at least 32 characters")
        if not self.postgres_password or self.postgres_password in INSECURE_SECRETS:
            problems.append("POSTGRES_PASSWORD is missing or is a known placeholder")
        if self.app_debug:
            problems.append("APP_DEBUG must be false in production")
        if problems:
            raise RuntimeError("Refusing to start in production: " + "; ".join(problems))


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.validate_for_production()
    return settings
