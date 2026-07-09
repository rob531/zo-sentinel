"""FastAPI application assembly -- the deployable entrypoint (uvicorn app.main:app).
Mounts auth + RBAC demo routes + health, and best-effort mounts any factory-built
feature router that exposes `router` (loose/unbuilt ones are skipped, never block boot).
"""
from __future__ import annotations
import importlib
from contextlib import asynccontextmanager

import pathlib

from fastapi import Depends, FastAPI
from fastapi.responses import HTMLResponse, RedirectResponse
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
    "score_dispute_api",
    # v1.1 Perspectives + v2 Ask slice (FATHER roadmap; direct-built 2026-07-02)
    "facet_enum_service", "perspective_admin_api", "perspective_query_api",
    "perspective_diff_service", "ask_corpus_indexer", "ask_answer_api",
    # real /api/dashboard/summary (the hollow factory one never mounted --
    # the SPA dashboard sat on 'loading' forever; treewalk fix 2026-07-02)
    "dashboard_summary_api",
    # vuln-intel spine + Scan-my-config killer feature (FATHER urgency ruling 2026-07-02)
    "vuln_osv_ingestor", "vuln_registry_linker", "vuln_exposure_api", "config_scan_api",
    # linker recall fix (repo->package identity) + OTX threat-intel context layer
    # (kill-switched OFF; council to rule on the provenance bar before arming)
    "vuln_pkg_enricher", "otx_threat_refs",
    # P2 vuln/OTX/CVE surfacing (DESIGN_NEXT_BUILD_TARGETS_2026_07; agent-built)
    "vuln_facet_extension", "vuln_coverage_sla_api",
    # P1 freshness gate (THE LINE: lands before any keyed/badge surface;
    # scorecard_badge_api stays unmounted until STALE-gating consumes this)
    "freshness_metadata_api",
    # cadence write path (CofC ruling 2026-07-08: NOT daemons; replaces
    # perspective_snapshot_daemon + ask_corpus_drift_guard candidates)
    "cadence_admin_api",
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
