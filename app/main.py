"""FastAPI application assembly -- the deployable entrypoint (uvicorn app.main:app).
Mounts auth + RBAC demo routes + health, and best-effort mounts any factory-built
feature router that exposes `router` (loose/unbuilt ones are skipped, never block boot).
"""
from __future__ import annotations
import importlib
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import auth
from .db import init_db
from .rbac import require_role
from .security import Principal, get_principal
from .settings import settings

# Factory-built single-file routers to mount when present + importable.
_OPTIONAL_ROUTERS = [
    "verdict_breakdown_api", "overview_dashboard_api", "org_entity_search_api",
    "entity_report_exporter", "org_api_key_manager", "verdict_watchlist_service",
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not settings.is_prod:        # dev/CI: ensure tables (Alembic owns prod schema)
        init_db()
    yield


app = FastAPI(title=settings.APP_NAME, version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS or ["*"],
    allow_methods=["*"], allow_headers=["*"], allow_credentials=True,
)


@app.get("/health")
def health():
    return {"status": "ok", "app": settings.APP_NAME, "env": settings.ENV}


@app.get("/rbac/whoami")
def whoami(principal: Principal = Depends(get_principal)):
    return principal


@app.get("/rbac/admin/ping")
def admin_ping(principal: Principal = Depends(require_role("admin"))):
    return {"ok": True, "as": principal.role}


app.include_router(auth.router)

for _modname in _OPTIONAL_ROUTERS:
    try:
        _m = importlib.import_module(_modname)
        _r = getattr(_m, "router", None)
        if _r is not None:
            app.include_router(_r)
    except Exception:
        pass  # loose/unbuilt feature module -- skip, never block boot
