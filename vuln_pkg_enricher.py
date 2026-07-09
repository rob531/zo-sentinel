"""vuln_pkg_enricher.py -- registry repo -> published-package identity.

THE recall fix for the vuln linker (audit 2026-07-03): 221,885 advisories are
99.98% npm and mostly identifiable ONLY by package name, while the registry's
80,539 servers carry ONLY repo identities (has_pkg was literally 0). The two
sides spoke different identity languages, so the exact-match linker could only
fire on the 13.5% of advisories that carry a repo reference -> 287 links.

This module reads each server repo's OWN manifest (package.json name /
pyproject [project].name) and stamps meta.ecosystem/meta.package, unlocking
vuln_identity.pkg_key on the server side. STILL within THE LINE: the mapping
comes verbatim from the repo's manifest -- exact, provenance-recorded
(pkg_identity_source + pkg_identity_at in meta), NEVER guessed. A repo whose
manifest can't be fetched or parsed, or is a manifest-less monorepo root, is
SKIPPED, not inferred.

The network fetch is an INJECTED seam (fetch_text callable) so the whole
select+parse+stamp path is hermetically testable and CI never hits the
network. RealManifestFetcher (live runs only) reads raw.githubusercontent.com.
Idempotent: servers already stamped are skipped. Bounded: --limit.

Run:  python3 vuln_pkg_enricher.py --live --limit 500     (tower/container)
API:  POST /api/vuln/enrich_packages  (admin-only, bounded)
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Callable, Optional

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry
from vuln_identity import repo_key
from verdict_breakdown_api import Principal, require_admin

router = APIRouter(prefix="/api", tags=["vuln"])

# fetch_text(url) -> body str, or None on any failure. Injected seam.
FetchText = Callable[[str], Optional[str]]

_PYPROJECT_NAME_RX = re.compile(
    r'^\s*name\s*=\s*["\']([A-Za-z0-9._-]+)["\']', re.MULTILINE)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def manifest_candidates(rk: str) -> list:
    """(url, ecosystem, parser) probes for one canonical repo key. GitHub only
    in v1 (95%+ of the registry); other hosts return [] -> skipped, not guessed."""
    m = re.match(r"repo:github\.com/([^/]+)/([^/]+)$", rk)
    if not m:
        return []
    base = f"https://raw.githubusercontent.com/{m.group(1)}/{m.group(2)}/HEAD"
    return [(f"{base}/package.json", "npm", parse_package_json),
            (f"{base}/pyproject.toml", "PyPI", parse_pyproject)]


def parse_package_json(body: str) -> Optional[str]:
    """Exact name from a package.json root. Private/unnamed -> None."""
    try:
        d = json.loads(body)
    except Exception:
        return None
    name = d.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    if d.get("private") is True:      # never published -> no advisory can name it
        return None
    return name.strip()


def parse_pyproject(body: str) -> Optional[str]:
    """Exact [project].name from pyproject.toml (regex on the standard form;
    anything odd -> None, never a guess)."""
    m = _PYPROJECT_NAME_RX.search(body or "")
    return m.group(1) if m else None


def enrich(db: Session, fetch_text: FetchText, limit: int = 200) -> dict:
    """Stamp meta.ecosystem/meta.package for up to `limit` unstamped servers
    that have a canonical repo identity. Returns stats. Idempotent."""
    stats = {"considered": 0, "stamped": 0, "no_manifest": 0,
             "already": 0, "no_repo_key": 0}
    for srv in db.execute(select(McpServerRegistry)).scalars():
        if stats["stamped"] + stats["no_manifest"] >= limit:
            break
        meta = {}
        if srv.meta:
            try:
                meta = json.loads(srv.meta)
            except Exception:
                meta = {}
        if not isinstance(meta, dict):
            meta = {}
        if meta.get("package") and meta.get("ecosystem"):
            stats["already"] += 1
            continue
        rk = repo_key(srv.url)
        if not rk:
            stats["no_repo_key"] += 1
            continue
        stats["considered"] += 1
        stamped = False
        for url, eco, parser in manifest_candidates(rk):
            body = fetch_text(url)
            if body is None:
                continue
            name = parser(body)
            if not name:
                continue
            meta.update({"ecosystem": eco, "package": name,
                         "pkg_identity_source": url,
                         "pkg_identity_at": _now().isoformat()})
            srv.meta = json.dumps(meta)
            stats["stamped"] += 1
            stamped = True
            break
        if not stamped:
            stats["no_manifest"] += 1
    db.commit()
    return stats


class RealManifestFetcher:
    """Live fetcher (constructed only for --live / API runs). Bounded timeout,
    404 -> None, any error -> None. No auth needed for public raw files."""

    def __init__(self, timeout: int = 15):
        self.timeout = timeout

    def __call__(self, url: str) -> Optional[str]:
        import urllib.request
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "zo-sentinel-pkg-enricher"})
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.read().decode("utf-8", "replace")
        except Exception:
            return None


@router.post("/vuln/enrich_packages")
def api_enrich(limit: int = 200, db: Session = Depends(get_session),
               principal: Principal = Depends(require_admin)) -> dict:
    return enrich(db, RealManifestFetcher(), limit=min(limit, 1000))


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--limit", type=int, default=200)
    args = ap.parse_args()
    if args.live:
        from app.db import SessionLocal
        with SessionLocal() as s:
            print(json.dumps(enrich(s, RealManifestFetcher(), args.limit)))
    else:
        # hermetic self-test (mirrors vuln_identity/linker __main__ pattern)
        assert parse_package_json('{"name": "@scope/pkg"}') == "@scope/pkg"
        assert parse_package_json('{"name": "x", "private": true}') is None
        assert parse_package_json("not json") is None
        assert parse_pyproject('[project]\nname = "my-pkg"\n') == "my-pkg"
        assert parse_pyproject("nothing here") is None
        cands = manifest_candidates("repo:github.com/o/r")
        assert [c[0] for c in cands] == [
            "https://raw.githubusercontent.com/o/r/HEAD/package.json",
            "https://raw.githubusercontent.com/o/r/HEAD/pyproject.toml"]
        assert manifest_candidates("repo:gitlab.com/o/r") == []
        print("PASS")
