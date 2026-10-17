"""Typed application settings, read once at import time.

Every tunable lives here rather than in scattered os.getenv calls, so the
full configuration surface of the app is one file you can read top to
bottom. Values are validated on load and the app refuses to start in
production with an insecure default - a misconfiguration should fail
loudly at boot, not silently weaken security for months.
"""

import os
from dataclasses import dataclass, field
from typing import List, Optional

# Sentinel for the development signing key. Production must override it;
# `_validate` below turns shipping this value into a startup crash.
INSECURE_SECRET = "supersecretkey"


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name).lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _env_list(name: str) -> List[str]:
    raw = _env(name)
    return [item.strip() for item in raw.split(",") if item.strip()]


class ConfigError(RuntimeError):
    """Raised at import time when the configuration is unusable."""


def normalize_database_url(url: str) -> str:
    """Pins the scheme and the driver.

    Two separate problems, both of which surface only at startup:

    1. Render and Heroku hand out "postgres://", a scheme SQLAlchemy 2
       stopped recognising.
    2. SQLAlchemy 2.1 changed the default driver for a bare "postgresql://"
       URL from psycopg2 to psycopg (v3). We install psycopg2-binary, so a
       bare URL started failing with ModuleNotFoundError the moment that
       release landed. Naming the driver explicitly means the installed
       version's default no longer decides which DBAPI gets imported.
    """
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return url


@dataclass(frozen=True)
class Settings:
    # --- environment -----------------------------------------------------
    environment: str = field(default_factory=lambda: _env("ENVIRONMENT", "development").lower())
    service_name: str = "endurancehub"
    version: str = field(default_factory=lambda: _env("GIT_COMMIT", "dev")[:7] or "dev")

    # --- security --------------------------------------------------------
    secret_key: str = field(default_factory=lambda: _env("SECRET_KEY", INSECURE_SECRET))
    # Short access tokens limit the damage from a leaked one; the refresh
    # token below is what keeps the user logged in without re-entering a
    # password, and unlike the access token it CAN be revoked.
    access_token_minutes: int = field(default_factory=lambda: _env_int("ACCESS_TOKEN_MINUTES", 30))
    refresh_token_days: int = field(default_factory=lambda: _env_int("REFRESH_TOKEN_DAYS", 30))

    # --- data ------------------------------------------------------------
    database_url: str = field(
        default_factory=lambda: _env(
            "DATABASE_URL", "postgresql://postgres:ARS231106@localhost:5432/endurancehub"
        )
    )
    db_pool_size: int = field(default_factory=lambda: _env_int("DB_POOL_SIZE", 5))
    db_max_overflow: int = field(default_factory=lambda: _env_int("DB_MAX_OVERFLOW", 5))

    # Optional. Absent means rate limiting falls back to a per-process
    # store and token revocation degrades to short token lifetimes only.
    redis_url: Optional[str] = field(default_factory=lambda: _env("REDIS_URL") or None)

    # --- http ------------------------------------------------------------
    # Empty means same-origin only, which is the production arrangement:
    # FastAPI serves the frontend, so no cross-origin request ever happens.
    cors_origins: List[str] = field(default_factory=lambda: _env_list("CORS_ORIGINS"))
    max_request_bytes: int = field(default_factory=lambda: _env_int("MAX_REQUEST_BYTES", 1_048_576))

    # --- observability ---------------------------------------------------
    sentry_dsn: Optional[str] = field(default_factory=lambda: _env("SENTRY_DSN") or None)
    log_level: str = field(default_factory=lambda: _env("LOG_LEVEL", "INFO").upper())
    # JSON logs are machine-readable for log aggregators; plain text is
    # easier to read while developing.
    json_logs: bool = field(default_factory=lambda: _env_bool("JSON_LOGS", True))

    # --- behaviour -------------------------------------------------------
    analytics_cache_seconds: int = field(default_factory=lambda: _env_int("ANALYTICS_CACHE_SECONDS", 60))
    # Analytics never need the full history; this bounds every query so a
    # user with ten years of data costs the same as one with one year.
    analytics_window_days: int = field(default_factory=lambda: _env_int("ANALYTICS_WINDOW_DAYS", 400))

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def is_testing(self) -> bool:
        return self.environment == "test"

    def _validate(self) -> None:
        if self.is_production:
            # The three ways a production deploy can be quietly unsafe.
            if self.secret_key == INSECURE_SECRET or len(self.secret_key) < 32:
                raise ConfigError(
                    "SECRET_KEY must be set to a unique value of at least 32 characters "
                    "in production. Tokens signed with the default key can be forged."
                )
            if "@localhost" in self.database_url or "@127.0.0.1" in self.database_url:
                raise ConfigError("DATABASE_URL still points at a local development database.")
        if self.access_token_minutes < 1:
            raise ConfigError("ACCESS_TOKEN_MINUTES must be at least 1.")


def _build() -> Settings:
    s = Settings()
    # Normalised once, here, so every consumer - the engine, Alembic's env,
    # the migration runner - gets the identical URL. Frozen dataclass, so
    # the write goes through object.__setattr__.
    object.__setattr__(s, "database_url", normalize_database_url(s.database_url))
    s._validate()
    return s


settings = _build()
