"""App configuration -- env-driven, with safe dev defaults. Twelve-factor: every
deploy knob is an environment variable (Heroku config vars / Cloud Run env / Secret
Manager). Secrets (JWT signing key, DB URL) come from the platform, sourced from
AgentVault on the tower side -- never hardcoded.
"""
from __future__ import annotations
import os


class Settings:
    APP_NAME = "MCPRisky"
    ENV = os.environ.get("APP_ENV", "dev")

    # Auth / JWT -- APP_JWT_SECRET MUST be set in any non-dev deploy.
    JWT_SECRET = os.environ.get("APP_JWT_SECRET", "dev-insecure-change-me")
    JWT_ALG = "HS256"
    ACCESS_TTL = int(os.environ.get("APP_ACCESS_TTL_SECONDS", "900"))        # 15 min
    REFRESH_TTL = int(os.environ.get("APP_REFRESH_TTL_SECONDS", "1209600"))  # 14 days

    # Data -- app data targets Postgres; sqlite default keeps dev/CI hermetic.
    DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./zo_sentinel_app.db")

    CORS_ORIGINS = [o.strip() for o in os.environ.get("APP_CORS_ORIGINS", "*").split(",") if o.strip()]

    @property
    def is_prod(self) -> bool:
        return self.ENV.lower() in ("prod", "production")


settings = Settings()
