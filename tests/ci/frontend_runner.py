#!/usr/bin/env python3
"""
frontend_runner.py -- compile + test the zo-sentinel app from a front-end
perspective, hermetically, in CI.

"Front end" here is the FastAPI ui_server.py (the app factory + REST surface)
plus the 14 committed HTML views (dashboards, admin forms, submission portal).
This runner is the front-end analogue of smoke_ladder.py and reuses its
Check/Tier/junit machinery. It is a recursive (short-circuit) ladder:

    FE0  html         every *.html parses + is non-empty
    FE1  html_interactive  admin_*/submission HTML carry input controls + JS wiring
    FE2  app_build    ui_server imports + create_app() builds the FastAPI app
    FE3  routes      drive the app in-process (Starlette TestClient) against a
                     mock write_service: /health, openapi contract, auth gating,
                     public submission intake

FE0/FE1 are pure and run anywhere. FE2/FE3 need (a) a mock write_service at
$ZO_WRITE_SERVICE and (b) ui_server's host path to exist (it does a module-level
mkdir on /home/workspace/zo_sentinel). run_ci_frontend.py stages both. ui_server
is a PROTECTED file and is never edited -- we boot it as-is via its factory.

If the host path cannot be staged (e.g. a local non-Linux dev box), FE2/FE3
degrade to SKIP-env rather than FAIL, so the runner is safe to run anywhere.
"""
from __future__ import annotations

import os
import sys
import time
import traceback
from html.parser import HTMLParser
from pathlib import Path

from tests.ci import hermetic_manifest as M
from tests.ci.smoke_ladder import Check, Tier

REPO_ROOT = M.REPO_ROOT

# HTML files expected to be interactive (carry a form). Everything else only
# needs to parse + be non-empty.
FORM_HTML_GLOBS = ("admin_*.html", "*submission*.html")


# =============================================================================
# A tolerant HTML structure inspector (stdlib only -- no lxml/bs4 dep)
# =============================================================================

class _Inspector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[str] = []
        self.has_form = False
        self.has_input_control = False
        self.has_body_content = False
        self.error: str | None = None

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        if tag == "form":
            self.has_form = True
        if tag in ("input", "select", "textarea", "button"):
            self.has_input_control = True
        if tag in ("div", "section", "main", "table", "h1", "h2", "p", "ul", "canvas"):
            self.has_body_content = True

    def handle_data(self, data):
        if data.strip():
            self.has_body_content = True


def _inspect_html(path: Path) -> _Inspector:
    insp = _Inspector()
    try:
        insp.feed(path.read_text(encoding="utf-8", errors="replace"))
    except Exception as e:  # HTMLParser is very tolerant; this is rare
        insp.error = f"{type(e).__name__}: {e}"
    return insp


def _html_files() -> list[Path]:
    return sorted(REPO_ROOT.glob("*.html"))


def _is_form_page(name: str) -> bool:
    from fnmatch import fnmatch
    return any(fnmatch(name, g) for g in FORM_HTML_GLOBS)


# =============================================================================
# FE0 -- every HTML view parses + is non-empty
# =============================================================================

def fe0_html() -> Tier:
    t = Tier(0, "html")
    files = _html_files()
    if not files:
        t.checks.append(Check("html::present", False, "no *.html files found at repo root"))
        return t
    quarantine = M.quarantined_html_files()
    for p in files:
        rel = p.relative_to(REPO_ROOT).as_posix()
        start = time.monotonic()
        insp = _inspect_html(p)
        ok = insp.error is None and insp.has_body_content and len(insp.tags) > 0
        if not ok and rel in quarantine:
            # Known placeholder stub -- surface as debt, don't gate on it.
            t.warnings.append(f"quarantined placeholder HTML: {rel}")
            continue
        detail = insp.error or ("empty / no renderable content" if not ok else "")
        t.checks.append(Check(f"html::{p.name}", ok, detail,
                              int((time.monotonic() - start) * 1000)))
    # Stale-quarantine hygiene: if a quarantined stub now renders, flag it so
    # the list gets pruned (mirrors the syntax-quarantine ratchet).
    for rel in quarantine:
        ap = REPO_ROOT / rel
        if ap.exists():
            insp = _inspect_html(ap)
            if insp.error is None and insp.has_body_content and len(insp.tags) > 0:
                t.checks.append(Check(
                    "html_quarantine_stale", False,
                    f"{rel} now renders; remove it from tests/ci/html_quarantine.txt"))
    return t


# =============================================================================
# FE1 -- interactive pages carry input controls AND are wired to act on them
# =============================================================================
# These admin/submission pages are fetch-driven SPAs: they have input controls
# submitted via JS (fetch/onclick), not a <form> POST. So the real contract is
# "interactive controls present AND wired", not "has a <form>". A page with
# inputs but no JS wiring would be dead UI -- that we DO flag.

_WIRING_TOKENS = ("fetch(", "onclick", "addEventListener", "onsubmit", "htmx")


def fe1_html_interactive() -> Tier:
    t = Tier(1, "html_interactive")
    pages = [p for p in _html_files() if _is_form_page(p.name)]
    if not pages:
        t.checks.append(Check("html_interactive::discovered", False,
                              f"no pages matched {FORM_HTML_GLOBS}"))
        return t
    for p in pages:
        insp = _inspect_html(p)
        src = p.read_text(encoding="utf-8", errors="replace")
        wired = any(tok in src for tok in _WIRING_TOKENS)
        ok = insp.has_input_control and wired
        form_note = "" if insp.has_form else " (no <form>; JS-driven)"
        detail = "" if ok else (
            f"input_control={insp.has_input_control} wired={wired}{form_note}")
        t.checks.append(Check(f"html_interactive::{p.name}", ok, detail))
    return t


# =============================================================================
# FE2 -- ui_server imports + create_app() builds the app
# =============================================================================

def _stage_host_path() -> tuple[bool, str]:
    """ui_server does a module-level mkdir on /home/workspace/zo_sentinel/logs.
    Try to make that path writable so the protected module imports unmodified.
    Returns (ok, reason)."""
    target = Path("/home/workspace/zo_sentinel/logs")
    try:
        target.mkdir(parents=True, exist_ok=True)
        # writability probe
        probe = target / ".ci_write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True, ""
    except Exception as e:
        return False, f"cannot stage host path {target}: {type(e).__name__}: {e}"


# module-level cache so FE3 can reuse the app FE2 built
_BUILT_APP = None


def fe2_app_build() -> Tier:
    global _BUILT_APP
    t = Tier(2, "app_build")

    staged, reason = _stage_host_path()
    if not staged:
        t.skipped = True
        t.skip_reason = reason
        return t

    ws = os.environ.get("ZO_WRITE_SERVICE", "http://127.0.0.1:8772")
    # ui_server hardcodes 127.0.0.1:8772; if the mock isn't there, create_app's
    # migrate_auth_tokens_table() still returns (helpers swallow errors), so the
    # build can proceed -- but we record whether the mock is reachable.
    try:
        import requests
        mock_up = requests.get(ws + "/health", timeout=3).status_code == 200
    except Exception:
        mock_up = False

    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))

    start = time.monotonic()
    try:
        import ui_server  # PROTECTED -- imported unmodified
        ok = hasattr(ui_server, "create_app")
        detail = "" if ok else "ui_server has no create_app()"
    except Exception as e:
        ok = False
        detail = f"import failed: {type(e).__name__}: {e}\n" + traceback.format_exc(limit=3)
    t.checks.append(Check("app_build::import_ui_server", ok, detail,
                          int((time.monotonic() - start) * 1000)))
    if not ok:
        return t

    start = time.monotonic()
    try:
        _BUILT_APP = ui_server.create_app()
        routes = [r.path for r in getattr(_BUILT_APP, "routes", [])]
        ok = _BUILT_APP is not None and "/health" in routes
        detail = f"routes={len(routes)} mock_reachable={mock_up}"
    except Exception as e:
        ok = False
        detail = f"create_app failed: {type(e).__name__}: {e}\n" + traceback.format_exc(limit=3)
    t.checks.append(Check("app_build::create_app", ok, detail,
                          int((time.monotonic() - start) * 1000)))
    return t


# =============================================================================
# FE3 -- drive the app in-process (TestClient) against the mock
# =============================================================================

def fe3_routes() -> Tier:
    t = Tier(3, "routes")
    if _BUILT_APP is None:
        t.skipped = True
        t.skip_reason = "app not built (FE2 skipped or failed)"
        return t

    try:
        from starlette.testclient import TestClient
    except Exception as e:
        t.checks.append(Check("routes::testclient_import", False,
                              f"{type(e).__name__}: {e} (need httpx installed)"))
        return t

    client = TestClient(_BUILT_APP)

    # /health is public and must be 200 + {status: ok}
    start = time.monotonic()
    try:
        r = client.get("/health")
        ok = r.status_code == 200 and r.json().get("status") == "ok"
        detail = "" if ok else f"HTTP {r.status_code}: {r.text[:120]}"
    except Exception as e:
        ok, detail = False, f"{type(e).__name__}: {e}"
    t.checks.append(Check("routes::health_public_200", ok, detail,
                          int((time.monotonic() - start) * 1000)))

    # openapi contract present + advertises the REST surface
    start = time.monotonic()
    try:
        r = client.get("/openapi.json")
        spec = r.json() if r.status_code == 200 else {}
        paths = set(spec.get("paths", {}).keys())
        expected = {"/health", "/api/servers", "/api/submissions", "/api/dashboard/summary"}
        missing = expected - paths
        ok = r.status_code == 200 and not missing
        detail = "" if ok else f"missing from openapi: {sorted(missing)}"
    except Exception as e:
        ok, detail = False, f"{type(e).__name__}: {e}"
    t.checks.append(Check("routes::openapi_contract", ok, detail,
                          int((time.monotonic() - start) * 1000)))

    # auth gate: a protected route must DENY (401/403) WITHOUT a token -- proves
    # the middleware is wired. A regression that opened it (2xx) fails here.
    start = time.monotonic()
    try:
        r = client.get("/api/auth/tokens")
        ok = r.status_code in (401, 403)
        detail = "" if ok else f"expected 401/403, got {r.status_code} (auth gate open?)"
    except Exception as e:
        ok, detail = False, f"{type(e).__name__}: {e}"
    t.checks.append(Check("routes::protected_route_denied_without_token", ok, detail,
                          int((time.monotonic() - start) * 1000)))

    # public submission intake accepts a POST without a token (it's whitelisted)
    start = time.monotonic()
    try:
        r = client.post("/api/submissions",
                        json={"server_name": "ci_smoke", "url": "https://example.invalid"})
        # Accept 2xx (stored) or a 4xx validation response -- NOT 401/5xx.
        ok = r.status_code not in (401, 500, 502, 503)
        detail = "" if ok else f"HTTP {r.status_code}: {r.text[:120]}"
    except Exception as e:
        ok, detail = False, f"{type(e).__name__}: {e}"
    t.checks.append(Check("routes::public_submission_intake", ok, detail,
                          int((time.monotonic() - start) * 1000)))
    return t


# =============================================================================
# Ladder
# =============================================================================

FE_LADDER = [
    (0, "html", fe0_html),
    (1, "html_interactive", fe1_html_interactive),
    (2, "app_build", fe2_app_build),
    (3, "routes", fe3_routes),
]


def run_frontend_ladder(stop_on_fail: bool = True) -> list[Tier]:
    results: list[Tier] = []
    broken_at = None
    for tid, name, fn in FE_LADDER:
        if broken_at is not None and stop_on_fail:
            results.append(Tier(tid, name, skipped=True,
                                skip_reason=f"short-circuited by FE{broken_at} failure"))
            continue
        print(f"\n=== FE{tid}: {name} ===")
        try:
            tier = fn()
        except Exception as e:
            tier = Tier(tid, name)
            tier.checks.append(Check(f"{name}::harness_error", False,
                                     f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=4)}"))
        results.append(tier)
        if tier.skipped:
            print(f"  [SKIP] {tier.skip_reason}")
        for c in tier.checks:
            print(f"  [{'PASS' if c.passed else 'FAIL'}] {c.name}"
                  + (f"  -- {c.detail.splitlines()[0]}" if c.detail else ""))
        for w in tier.warnings:
            print(f"  [warn] {w}")
        # A SKIP-env tier (no checks) does not break the ladder; a tier with a
        # failing check does.
        if not tier.skipped and not tier.passed:
            broken_at = tid
    return results
