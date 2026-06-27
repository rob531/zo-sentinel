"""FastAPI application assembly -- the deployable entrypoint (uvicorn app.main:app).
Mounts auth + RBAC demo routes + health, and best-effort mounts any factory-built
feature router that exposes `router` (loose/unbuilt ones are skipped, never block boot).
"""
from __future__ import annotations
import importlib
from contextlib import asynccontextmanager

import pathlib

from fastapi import Depends, FastAPI
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware

from . import auth
from .db import init_db
from .rbac import require_role
from .security import Principal, get_principal
from .settings import settings

# Factory-built single-file routers to mount when present + importable.
_OPTIONAL_ROUTERS = [
    "server_compare_api",
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

_STATIC = pathlib.Path(__file__).parent / "static"

import os as _os
def _render(name: str) -> str:
    html = (_STATIC / name).read_text(encoding="utf-8")
    return html.replace("__CLERK_PK__", _os.getenv("CLERK_PUBLISHABLE_KEY", ""))



@app.get("/", response_class=HTMLResponse)
def consent_gate():
    """Clickwrap evaluation-only notice -- the assertion shown before site access."""
    return _render("landing.html")


@app.get("/disclaimer", response_class=HTMLResponse)
def disclaimer_page():
    return (_STATIC / "consent_gate.html").read_text(encoding="utf-8")

for _modname in _OPTIONAL_ROUTERS:
    try:
        _m = importlib.import_module(_modname)
        _r = getattr(_m, "router", None)
        if _r is not None:
            app.include_router(_r)
    except Exception:
        pass  # loose/unbuilt feature module -- skip, never block boot


@app.get("/app", response_class=HTMLResponse)
def app_page():
    return _render("app.html")


@app.get("/app/{rest:path}", response_class=HTMLResponse)
def app_spa(rest: str):
    return _render("app.html")


@app.middleware("http")
async def _security_headers(request, call_next):
    resp = await call_next(request)
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    resp.headers["Content-Security-Policy-Report-Only"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://*.clerk.accounts.dev https://*.clerk.com; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data: https:; "
        "connect-src 'self' https://*.clerk.accounts.dev https://*.clerk.com https://api.clerk.com; "
        "frame-src https://*.clerk.accounts.dev https://*.clerk.com; "
        "worker-src 'self' blob:; base-uri 'self'; frame-ancestors 'none'"
    )
    return resp
