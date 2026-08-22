"""FastAPI application assembly -- the deployable entrypoint (uvicorn app.main:app).
Mounts auth + RBAC demo routes + health, and best-effort mounts any factory-built
feature router that exposes `router` (loose/unbuilt ones are skipped, never block boot).
"""
from __future__ import annotations
from contextlib import asynccontextmanager

import pathlib

from fastapi import Depends, FastAPI
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware

from . import auth, clerk_webhook
from .db import init_db
from .rbac import require_role
from .security import Principal, get_principal
from .settings import settings
from .ask_router import router as ask_router

# Factory-built routers are no longer hand-listed here. The SOA spine
# (services/active/ -> app/_spine_generated.py) is the source of truth; see below.
# Census parity (do NOT delete without updating tools/reachability_deferred.json):
# these siblings are DOCUMENTED-UNMOUNTED and were named in the old list's comments.
# Keeping the names here holds the reachability census delta-neutral across this
# refactor -- their triage (mount / redirect / delete) is Step 6, not this PR:
#   freshness_gate  scorecard_badge_api  threat_intel_reference_api  server_threat_intel_status_api


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
app.include_router(clerk_webhook.router)
app.include_router(ask_router)

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

# --- SOA spine (FU-039/072; CofC 2026-07-23) --------------------------------
# Mounts are GENERATED at build time from services/active/ into
# app/_spine_generated.py by tools/generate_spine.py, and fail LOUD: CI raises
# via `generate_spine.py --strict`; prod boots anyway but records every outcome
# on app.state (surfaced at /spine/health) + logs. This REPLACES the old silent
# `except Exception: pass` loop -- the invisibility bug the reachability
# postmortem (FU-044) exists to kill. Source of truth is services/active/.
from ._spine_generated import include_spine
include_spine(app)  # boot-anyway in prod; strict raise is a CI-only gate


@app.get("/spine/health")
def spine_health():
    """Visible mount status for the SOA spine -- the anti-invisibility surface.
    mounted / skipped_no_router / failures are recorded by include_spine()."""
    st = app.state
    failures = getattr(st, "spine_mount_failures", [])
    return {
        "ok": not failures,
        "service_count": getattr(st, "spine_service_count", 0),
        "mounted": getattr(st, "spine_mounted", []),
        "skipped_no_router": getattr(st, "spine_skipped_no_router", []),
        "failures": failures,
    }


_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _render_root(name: str) -> str:
    """Serve a repo-root view file (the factory/spec-canonical filenames) with
    the same Clerk-PK injection app/static pages get."""
    html = (_REPO_ROOT / name).read_text(encoding="utf-8")
    return html.replace("__CLERK_PK__", _os.getenv("CLERK_PUBLISHABLE_KEY", ""))


@app.get("/perspectives", response_class=HTMLResponse)
def perspectives_page():
    """v1.1 Perspectives: deterministic faceted views + trust-diff."""
    return _render_root("perspective_tree_view.html")


@app.get("/scan", response_class=HTMLResponse)
def scan_page():
    """Killer feature: paste an mcp.json, get a provenance-cited risk report."""
    return _render_root("scan_view.html")


@app.get("/dispute", response_class=HTMLResponse)
def dispute_page():
    """User-facing score-dispute form (closes the dispute-UI frontend gap; backend live since 2026-06-28)."""
    return _render_root("dispute_view.html")


@app.get("/ask", response_class=HTMLResponse)
def ask_page():
    """v2 slice: grounded Ask with mandatory citations."""
    return _render_root("ask_search_view.html")


@app.get("/threat-intel", response_class=HTMLResponse)
def threat_intel_page():
    """P2 vuln/OTX/CVE surfacing: per-server advisories + threat-intel refs
    (curated vs aggregator), INSUFFICIENT when the kill-switch is off."""
    return _render_root("server_threat_intel_view.html")


@app.get("/roadmap", response_class=HTMLResponse)
def roadmap_page():
    """FATHER launch condition: the public roadmap, so v1 reads as a
    foundation, not 'just a lookup'."""
    return _render_root("roadmap_announcement.html")


@app.get("/dashboard/exemptions", response_class=HTMLResponse)
def exemptions_dashboard_page():
    """Exemptions dashboard: manage and view MCP server risk-score exemptions."""
    return _render_root("mcp_exemptions_dashboard_view.html")


@app.get("/explore")
def explore_redirect():
    """Deep-link fix (treewalk 2026-07-03 gap #7): /explore as a raw URL 404'd;
    only the client-routed /app/explore worked. Permanent redirect keeps old
    links and address-bar guesses working."""
    return RedirectResponse("/app/explore", status_code=308)


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


# --- Vanity-domain redirects -------------------------------------------------
# Defensive/marketing domains all 301 to the canonical site. Suffix-matched so this
# only fires for these hosts -- mcplookup.app, www, *.fly.dev and health checks are
# never affected. Certs for these hosts are on the Fly app; DNS A/AAAA -> Fly.
_VANITY_SUFFIXES = (
    "mcprisky.io", "mcprisky.app", "mcpcheck.app", "mcpcheck.cloud",
    "mcpcheck.space", "mcpcheck.one", "mcpcheck.bot", "mcpcheck.wiki",
    "mcpchecker.app", "mcpchecker.cloud", "mcpchecker.wiki",
)
import os as _os_pivot  # env-driven canonical so a pivot is a secret change, not a code edit
# Pivot primary domain:  flyctl secrets set CANONICAL_HOST=<domain> -a mcplookup  (docs/DOMAIN_PIVOT_RUNBOOK.md)
# Default unchanged (mcplookup.app) until the secret is set.
_CANONICAL_HOST = (_os_pivot.environ.get("CANONICAL_HOST") or "mcplookup.app").strip().lower()
# All hosted domains = the vanity list + the historic primary; canonical served, the rest 301 to it.
_ALL_HOSTED_DOMAINS = _VANITY_SUFFIXES + ("mcplookup.app",)
_VANITY_SUFFIXES = tuple(d for d in _ALL_HOSTED_DOMAINS if d != _CANONICAL_HOST)


@app.middleware("http")
async def _vanity_redirect(request, call_next):
    host = (request.headers.get("host") or "").split(":")[0].lower()
    if host and host != _CANONICAL_HOST and host.endswith(_VANITY_SUFFIXES):
        target = f"https://{_CANONICAL_HOST}{request.url.path}"
        if request.url.query:
            target += "?" + request.url.query
        return RedirectResponse(target, status_code=301)
    return await call_next(request)


# --- Self-test ---------------------------------------------------------------
if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from unittest.mock import patch

    # Mock auth so the dashboard route is accessible without a real session
    with patch("app.security.get_principal") as mock_principal:
        mock_principal.return_value = Principal(
            user_id="test-user",
            org_id="test-org",
            role="admin",
            email="test@example.com",
        )
        client = TestClient(app)
        resp = client.get("/dashboard/exemptions")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
        assert "MCP Exemptions Dashboard" in resp.text, (
            "Expected dashboard title not found in response"
        )
        print("PASS")
