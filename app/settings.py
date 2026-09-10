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

    # Clerk -- the front door has been Clerk-only in the BROWSER since June;
    # these are the server-side halves. All default to empty so dev/CI stay
    # hermetic: with no secret the webhook returns 503 and writes nothing,
    # which is the correct unconfigured state for an endpoint that touches
    # `users`. Real values come from AgentVault via Fly secrets, never a file.
    CLERK_WEBHOOK_SECRET = os.environ.get("CLERK_WEBHOOK_SECRET", "")
    CLERK_SECRET_KEY = os.environ.get("CLERK_SECRET_KEY", "")
    CLERK_DEFAULT_ORG = os.environ.get("CLERK_DEFAULT_ORG", "public")
    # Hours a Clerk signup may be older than its row before the nightly
    # reconcile calls the webhook dead. 2h is comfortably beyond Svix's retry
    # schedule, so a slow delivery does not read as an outage.
    CLERK_WEBHOOK_STALE_HOURS = float(os.environ.get("CLERK_WEBHOOK_STALE_HOURS", "2"))

    @property
    def is_prod(self) -> bool:
        return self.ENV.lower() in ("prod", "production")


settings = Settings()
