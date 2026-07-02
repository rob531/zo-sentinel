"""vuln_osv_ingestor.py -- ingest OSV advisories into vuln_advisories.

Starts the 4-weeks-stable-ingestion CLOCK (FATHER 2026-07-02 compressed
ruling). OSV.dev is the aggregator for GHSA/npm/PyPI/Go/etc.; each record
carries its own id, affected package coords, version ranges, severity, and
references -- everything THE LINE requires (source_url + timestamp + exact
identity). NO fuzzy inference: a record we can't normalize is SKIPPED, not
guessed.

The network fetch is an INJECTED seam (fetch_batch callable) so the whole
normalize+upsert+provenance path is hermetically testable with fixtures and CI
never hits the network. RealOsvFetcher (constructed only for live runs) uses
the OSV export/query API. Idempotent: content_hash short-circuits unchanged
records.

Run:  python3 vuln_osv_ingestor.py --live --ecosystem npm   (tower/container)
API:  POST /api/vuln/ingest  (admin-only)
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory
from vuln_identity import advisory_identities
from verdict_breakdown_api import Principal, require_admin

router = APIRouter(prefix="/api", tags=["vuln"])
OSV_SOURCE_BASE = "https://osv.dev/vulnerability/"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _severity_from(record: dict) -> str:
    """Normalize OSV severity (CVSS vector or database_specific) to a tier."""
    db = record.get("database_specific") or {}
    sev = (db.get("severity") or "").upper()
    if sev in ("CRITICAL", "HIGH", "MEDIUM", "MODERATE", "LOW"):
        return "MEDIUM" if sev == "MODERATE" else sev
    for s in record.get("severity", []) or []:
        score = str(s.get("score", ""))
        # CVSS base score buckets (deterministic, no external lookup)
        m = None
        for part in score.replace("/", " ").split():
            try:
                m = float(part)
                break
            except ValueError:
                continue
        if m is not None:
            return ("CRITICAL" if m >= 9 else "HIGH" if m >= 7 else
                    "MEDIUM" if m >= 4 else "LOW")
    return "UNKNOWN"


def normalize(record: dict) -> Optional[dict]:
    """One OSV record -> a normalized advisory dict, or None if it can't be
    deterministically identified (no id, or no package/repo identity)."""
    adv_id = record.get("id")
    if not adv_id:
        return None
    affected = record.get("affected", []) or []
    pkg_name = ecosystem = None
    ranges: List[dict] = []
    for a in affected:
        p = a.get("package") or {}
        if p.get("name"):
            pkg_name = pkg_name or p.get("name")
            ecosystem = ecosystem or p.get("ecosystem")
        for r in a.get("ranges", []) or []:
            ranges.append({"type": r.get("type"),
                           "events": r.get("events", [])})
    repo_refs = [r.get("url") for r in record.get("references", []) or []
                 if r.get("url")]
    idents = advisory_identities(ecosystem, pkg_name, None, repo_refs)
    if not idents:
        return None                          # cannot link deterministically -> skip
    feed = "ghsa" if str(adv_id).startswith("GHSA") else \
           "nvd" if str(adv_id).startswith("CVE") else "osv"
    aliases = record.get("aliases", []) or []
    source_url = (record.get("database_specific", {}).get("url")
                  or (repo_refs[0] if repo_refs else OSV_SOURCE_BASE + adv_id))
    published = record.get("published")
    return {
        "id": adv_id, "feed": feed,
        "summary": (record.get("summary") or record.get("details") or "")[:2000],
        "severity": _severity_from(record), "ecosystem": ecosystem,
        "package": pkg_name, "affected_ranges": ranges, "aliases": aliases,
        "source_url": source_url, "published_at": published,
        "_identities": sorted(idents),
    }


def _hash(adv: dict) -> str:
    return hashlib.sha256(json.dumps(
        {k: adv[k] for k in ("severity", "package", "ecosystem",
                             "affected_ranges", "summary", "_identities")},
        sort_keys=True, default=str).encode()).hexdigest()[:16]


def ingest(db: Session, fetch_batch: Callable[[], List[dict]]) -> dict:
    """Pull one batch of OSV records, normalize, upsert. Idempotent."""
    stats = {"fetched": 0, "written": 0, "skipped_unidentifiable": 0,
             "unchanged": 0}
    for record in fetch_batch():
        stats["fetched"] += 1
        adv = normalize(record)
        if adv is None:
            stats["skipped_unidentifiable"] += 1
            continue
        h = _hash(adv)
        row = db.get(VulnAdvisory, adv["id"])
        if row is not None and row.content_hash == h:
            stats["unchanged"] += 1
            continue
        if row is None:
            row = VulnAdvisory(id=adv["id"])
            db.add(row)
        for f in ("feed", "summary", "severity", "ecosystem", "package",
                  "affected_ranges", "aliases", "source_url"):
            setattr(row, f, adv[f])
        row.identities = adv["_identities"]
        if adv.get("published_at"):
            try:
                row.published_at = datetime.fromisoformat(
                    str(adv["published_at"]).replace("Z", "+00:00"))
            except Exception:
                pass
        row.content_hash = h
        row.fetched_at = _now()
        stats["written"] += 1
    db.commit()
    return stats


@router.post("/vuln/ingest")
def api_ingest(db: Session = Depends(get_session),
               principal: Principal = Depends(require_admin)) -> dict:
    from vuln_osv_ingestor import RealOsvFetcher   # lazy: no network import in CI
    return ingest(db, RealOsvFetcher().fetch_batch)


class RealOsvFetcher:
    """Live OSV fetch (constructed only for --live / the admin route). Pulls
    the OSV ecosystem export zips; kept out of the hermetic import path."""

    def __init__(self, ecosystem: str = "npm"):
        self.ecosystem = ecosystem

    def fetch_batch(self) -> List[dict]:   # pragma: no cover (network)
        import io
        import urllib.request
        import zipfile
        url = f"https://osv-vulnerabilities.storage.googleapis.com/{self.ecosystem}/all.zip"
        with urllib.request.urlopen(url, timeout=120) as r:
            data = r.read()
        out = []
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for n in z.namelist():
                if n.endswith(".json"):
                    try:
                        out.append(json.loads(z.read(n)))
                    except Exception:
                        continue
        return out


if __name__ == "__main__":
    import os, sys
    os.environ.setdefault("DATABASE_URL", "sqlite://")
    if "--live" in sys.argv:
        from app.db import SessionLocal
        eco = "npm"
        if "--ecosystem" in sys.argv:
            eco = sys.argv[sys.argv.index("--ecosystem") + 1]
        print(ingest(SessionLocal(), RealOsvFetcher(eco).fetch_batch))
        raise SystemExit(0)
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db import Base
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    fixture = [
        {"id": "GHSA-xxxx-1", "summary": "Auth bypass",
         "database_specific": {"severity": "HIGH", "url": "https://github.com/advisories/GHSA-xxxx-1"},
         "affected": [{"package": {"ecosystem": "npm", "name": "@mcp/inspector"},
                       "ranges": [{"type": "SEMVER", "events": [{"introduced": "0"},
                                                                {"fixed": "1.2.3"}]}]}],
         "references": [{"url": "https://github.com/anthropics/mcp-inspector"}],
         "aliases": ["CVE-2025-49596"], "published": "2025-06-01T00:00:00Z"},
        {"id": "MAL-nopkg", "affected": [{}]},     # unidentifiable -> skipped
    ]
    st = ingest(s, lambda: fixture)
    assert st["written"] == 1 and st["skipped_unidentifiable"] == 1, st
    row = s.get(VulnAdvisory, "GHSA-xxxx-1")
    assert row.severity == "HIGH" and row.package == "@mcp/inspector"
    assert row.source_url.startswith("https://")          # provenance present
    assert "CVE-2025-49596" in (row.aliases or [])
    st2 = ingest(s, lambda: fixture)                       # idempotent
    assert st2["written"] == 0 and st2["unchanged"] == 1, st2
    print("PASS")
