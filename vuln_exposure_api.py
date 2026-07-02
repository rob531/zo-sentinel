"""vuln_exposure_api.py -- GET /api/servers/{id}/vulns (provenance-first).

THE LINE (council 2026-07-02): no vuln claim without a verifiable source_url +
timestamp + confidence, and the whole surface degrades to INSUFFICIENT when the
kill-switch (policy vuln.enabled) is off. Every returned advisory carries its
provenance fields verbatim from vuln_advisories/vuln_links -- the API cannot
emit a claim it can't source.
"""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory, VulnLink
from verdict_breakdown_api import Principal, get_principal

router = APIRouter(prefix="/api", tags=["vuln"])

_SEV_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "UNKNOWN": 4}


def kill_switch_on() -> bool:
    try:
        from zo_sentinel import policy
        return policy.flag("vuln.enabled")
    except Exception:
        return False   # fail-closed: unknown state => surface is OFF


def server_vulns(db: Session, server_id: str) -> dict:
    """{status, vulns:[{id, severity, summary, source_url, published_at,
    fetched_at, match_basis, match_confidence}]}. status='disabled' when the
    kill-switch is off (=> the caller renders INSUFFICIENT, never a guess)."""
    if not kill_switch_on():
        return {"status": "disabled", "server_id": server_id, "vulns": []}
    rows = db.execute(
        select(VulnLink, VulnAdvisory)
        .join(VulnAdvisory, VulnAdvisory.id == VulnLink.advisory_id)
        .where(VulnLink.server_id == server_id)).all()
    vulns: List[dict] = []
    for link, adv in rows:
        vulns.append({
            "id": adv.id, "feed": adv.feed, "severity": adv.severity or "UNKNOWN",
            "summary": adv.summary, "source_url": adv.source_url,
            "published_at": adv.published_at.isoformat() if adv.published_at else None,
            "fetched_at": adv.fetched_at.isoformat() if adv.fetched_at else None,
            "match_basis": link.match_basis, "match_value": link.match_value,
            "match_confidence": link.match_confidence,
        })
    vulns.sort(key=lambda v: (_SEV_ORDER.get(v["severity"], 4), v["id"]))
    return {"status": "ok", "server_id": server_id, "count": len(vulns),
            "vulns": vulns}


@router.get("/servers/{server_id}/vulns")
def get_server_vulns(server_id: str, db: Session = Depends(get_session),
                     principal: Principal = Depends(get_principal)) -> dict:
    return server_vulns(db, server_id)


if __name__ == "__main__":
    import os
    os.environ["ZO_VULN_ENABLED"] = "1"     # arm the kill-switch for the test
    os.environ.setdefault("DATABASE_URL", "sqlite://")
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db import Base
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    s.add_all([
        VulnAdvisory(id="GHSA-1", feed="ghsa", severity="CRITICAL",
                     summary="rce", source_url="https://github.com/advisories/GHSA-1"),
        VulnAdvisory(id="GHSA-2", feed="ghsa", severity="LOW",
                     summary="info leak", source_url="https://github.com/advisories/GHSA-2"),
        VulnLink(advisory_id="GHSA-1", server_id="s1", match_basis="repo_exact",
                 match_value="repo:github.com/o/r", match_confidence=1.0),
        VulnLink(advisory_id="GHSA-2", server_id="s1", match_basis="repo_exact",
                 match_value="repo:github.com/o/r", match_confidence=1.0),
    ])
    s.commit()
    r = server_vulns(s, "s1")
    assert r["status"] == "ok" and r["count"] == 2
    assert r["vulns"][0]["id"] == "GHSA-1"            # CRITICAL first
    assert all(v["source_url"].startswith("https://") for v in r["vulns"])  # provenance
    # kill-switch off -> disabled, no claims
    os.environ["ZO_VULN_ENABLED"] = "0"
    assert server_vulns(s, "s1")["status"] == "disabled"
    print("PASS")
