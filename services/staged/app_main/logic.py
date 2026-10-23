"""
services/staged/app_main/logic.py

FastAPI application entry point for the `app_main` service.
It assembles all routers defined in `app_router_registry` and provides
common utility functions used across the code‑base.

All data access is performed via the real application database layer:
`app.db.get_session` and the models exported from `app.models`.
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import Any, List, Tuple

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ----------------------------------------------------------------------
# Database / Models
# ----------------------------------------------------------------------
from app.db import get_session  # real SQLAlchemy session provider
from app.models import *  # import all real models (ServerRegistry, LLMAxisScore, etc.)

# ----------------------------------------------------------------------
# Router registry
# ----------------------------------------------------------------------
try:
    from app_router_registry import ROUTERS  # expected to be List[Tuple[APIRouter, str]]
except Exception as exc:  # pragma: no cover
    raise ImportError("Failed to import ROUTERS from app_router_registry") from exc

# ----------------------------------------------------------------------
# Logging
# ----------------------------------------------------------------------
log = logging.getLogger("app_main")
log.setLevel(logging.INFO)
if not log.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s | %(message)s"
    )
    handler.setFormatter(formatter)
    log.addHandler(handler)

# ----------------------------------------------------------------------
# Application factory
# ----------------------------------------------------------------------
_start_time: float = 0.0


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan context manager."""
    global _start_time
    _start_time = time.time()
    log.info("🚀 app_main startup")
    try:
        yield
    finally:
        log.info("🛑 app_main shutdown")


app = FastAPI(lifespan=lifespan)

# CORS – allow local development origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost", "http://127.0.0.1"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----------------------------------------------------------------------
# Include routers
# ----------------------------------------------------------------------
mounted_count = 0
for router, prefix in ROUTERS:  # type: ignore[attr-defined]
    app.include_router(router, prefix=prefix)
    mounted_count += 1
log.info(f"Mounted {mounted_count} routers from app_router_registry")

# ----------------------------------------------------------------------
# Health endpoint
# ----------------------------------------------------------------------


class HealthResponse(BaseModel):
    status: str
    routers_mounted: int
    uptime_seconds: float


@app.get("/health", response_model=HealthResponse, tags=["health"])
async def health_endpoint() -> HealthResponse:
    """Simple health check returning status, router count and uptime."""
    uptime = time.time() - _start_time if _start_time else 0.0
    return HealthResponse(
        status="ok",
        routers_mounted=mounted_count,
        uptime_seconds=round(uptime, 2),
    )


# ----------------------------------------------------------------------
# Utility functions used throughout the project
# ----------------------------------------------------------------------


def _db_session():
    """Dependency that yields a SQLAlchemy session."""
    return get_session()


# 1. Search registry -----------------------------------------------------


def search_registry(*, db=Depends(_db_session)) -> List[ServerRegistry]:
    """Return all server registry entries."""
    return db.query(ServerRegistry).all()  # type: ignore[name-defined]


# 2. Axis scores ----------------------------------------------------------


def get_all_axis_scores(*, db=Depends(_db_session)) -> List[LLMAxisScore]:
    """Return all LLM axis scores."""
    return db.query(LLMAxisScore).all()  # type: ignore[name-defined]


# 3. Heartbeat ------------------------------------------------------------


async def send_heartbeat(service_name: str) -> bool:
    """Placeholder heartbeat sender – logs and returns True."""
    log.info(f"Heartbeat sent for service: {service_name}")
    return True


# 4. Package facets -------------------------------------------------------


def compile_package_facets(*, db=Depends(_db_session)) -> dict:
    """Compile package facets – currently returns an empty dict."""
    # Real implementation would aggregate data from relevant tables.
    return {}


def compile_package_facets_endpoint() -> dict:
    """FastAPI‑compatible wrapper for `compile_package_facets`."""
    return compile_package_facets()


# 5. Startup / health helpers --------------------------------------------


async def startup_event():
    """Hook that can be used by other modules during startup."""
    log.info("startup_event hook executed")


def health() -> dict:
    """Legacy health helper used by older modules."""
    return {"status": "ok", "uptime_seconds": time.time() - _start_time}


# 6. Export job -----------------------------------------------------------


def create_export_job(*, db=Depends(_db_session), **kwargs: Any) -> int:
    """Create an export job record – returns a dummy job id."""
    # In a real implementation this would INSERT into an ExportJob table.
    log.info(f"Export job created with params: {kwargs}")
    return 1


def validate_export_request(payload: dict) -> None:
    """Validate export request payload – raises HTTPException on error."""
    if not payload:
        raise HTTPException(status_code=400, detail="Empty export request")
    # Additional validation logic would go here.


# 7. Unscored servers sample ---------------------------------------------


def get_unscored_servers_sample(limit: int = 10, *, db=Depends(_db_session)) -> List[ServerRegistry]:
    """Return a sample of servers without associated scores."""
    # Placeholder: return first `limit` rows.
    return db.query(ServerRegistry).limit(limit).all()  # type: ignore[name-defined]


# 8. Threat counts by tier -----------------------------------------------


def get_threat_counts_by_tier(*, db=Depends(_db_session)) -> dict:
    """Return a mapping of threat tier to count – empty placeholder."""
    return {}


# 9. Recent submissions ---------------------------------------------------


def get_recent_submissions(limit: int = 20, *, db=Depends(_db_session)) -> List[ScoreDispute]:
    """Return recent score dispute submissions."""
    return db.query(ScoreDispute).order_by(ScoreDispute.id.desc()).limit(limit).all()  # type: ignore[name-defined]


# 10. WebSocket execution -------------------------------------------------


async def ws_execute(message: str, *, db=Depends(_db_session)) -> str:
    """Echo‑like placeholder for WebSocket execution."""
    # Real logic would parse the message and interact with the DB.
    await asyncio.sleep(0)  # keep coroutine nature
    return f"executed: {message}"


# 11. Trust score computation ---------------------------------------------


def compute_trust_score_at_point(server_id: int, timestamp: float, *, db=Depends(_db_session)) -> float:
    """Compute a trust score – returns a dummy constant."""
    # Real implementation would aggregate scores up to `timestamp`.
    return 0.5


# 12. Perspective diff run ------------------------------------------------


def run(*, db=Depends(_db_session)) -> None:
    """Placeholder for the perspective diff run routine."""
    log.info("Perspective diff run executed")


# 13. Signal handling ------------------------------------------------------


def signal_handler(signum, frame) -> None:  # pragma: no cover
    """Log receipt of a signal."""
    log.info(f"Received signal {signum}, exiting gracefully.")


# 14. Verdict distribution -------------------------------------------------


def get_verdict_distribution(*, db=Depends(_db_session)) -> dict:
    """Return verdict distribution – empty placeholder."""
    return {}


# 15. Weekly digest generation --------------------------------------------


def generate_weekly_digest(*, db=Depends(_db_session)) -> str:
    """Generate a weekly digest – returns a placeholder string."""
    return "Weekly digest content placeholder"


# ----------------------------------------------------------------------
# Export symbols for external modules
# ----------------------------------------------------------------------
__all__ = [
    "app",
    "search_registry",
    "get_all_axis_scores",
    "send_heartbeat",
    "compile_package_facets",
    "compile_package_facets_endpoint",
    "startup_event",
    "health",
    "create_export_job",
    "validate_export_request",
    "get_unscored_servers_sample",
    "get_threat_counts_by_tier",
    "get_recent_submissions",
    "ws_execute",
    "compute_trust_score_at_point",
    "run",
    "signal_handler",
    "get_verdict_distribution",
    "generate_weekly_digest",
]