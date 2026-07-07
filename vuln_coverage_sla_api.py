"""vuln_coverage_sla_api.py -- GET /api/vuln/coverage (internal coverage-SLA).

P2 of docs/DESIGN_NEXT_BUILD_TARGETS_2026_07.md: the coverage-SLA metric
DESIGN_VULN_INTEL_HORIZON requires BEFORE any paid/keyed vuln surface ships.
Answers "how much of the registry does our vuln linkage actually cover, and
how fresh is the feed?" -- so the sales claim is a measured number, not vibes.

Shape: {status, registry_total, linked_servers, coverage_pct,
        newest_advisory_fetched_at}
Kill-switch aware (policy vuln.enabled): off => status=disabled, no numbers --
an off surface must not advertise coverage it refuses to serve.
Exemplar: dashboard_summary_api.py (same aggregate + TTL-cache pattern).
"""
from __future__ import annotations

import time
from typing import Dict

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, VulnAdvisory, VulnLink
from verdict_breakdown_api import Principal, get_principal

router = APIRouter(prefix="/api", tags=["vuln"])

_CACHE: dict = {"at": 0.0, "data": None}
CACHE_TTL_SECS = 300


def kill_switch_on() -> bool:
    try:
        from zo_sentinel import policy
        return policy.flag("vuln.enabled")
    except Exception:
        return False   # fail-closed


def compute_coverage(db: Session) -> Dict:
    registry_total = db.execute(
        select(func.count()).select_from(McpServerRegistry)).scalar() or 0
    linked_servers = db.execute(
        select(func.count(func.distinct(VulnLink.server_id)))).scalar() or 0
    newest = db.execute(select(func.max(VulnAdvisory.fetched_at))).scalar()
    pct = round(100.0 * linked_servers / registry_total, 2) if registry_total else 0.0
    return {"status": "ok",
            "registry_total": int(registry_total),
            "linked_servers": int(linked_servers),
            "coverage_pct": pct,
            "newest_advisory_fetched_at": newest.isoformat() if newest else None}


@router.get("/vuln/coverage")
def vuln_coverage(db: Session = Depends(get_session),
                  principal: Principal = Depends(get_principal)) -> dict:
    if not kill_switch_on():
        return {"status": "disabled"}
    now = time.time()
    if _CACHE["data"] is None or now - _CACHE["at"] > CACHE_TTL_SECS:
        _CACHE["data"] = compute_coverage(db)
        _CACHE["at"] = now
    return _CACHE["data"]


if __name__ == "__main__":
    import os
    from datetime import datetime
    os.environ["ZO_VULN_ENABLED"] = "1"
    os.environ.setdefault("DATABASE_URL", "sqlite://")
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db import Base
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    s.add_all([
        McpServerRegistry(server_id="a"), McpServerRegistry(server_id="b"),
        McpServerRegistry(server_id="c"), McpServerRegistry(server_id="d"),
        VulnAdvisory(id="CVE-1", feed="osv", source_url="https://osv.dev/CVE-1",
                     fetched_at=datetime(2026, 7, 4, 12, 0)),
        VulnLink(advisory_id="CVE-1", server_id="a", match_basis="repo_exact",
                 match_value="x", match_confidence=1.0),
        VulnLink(advisory_id="CVE-1", server_id="b", match_basis="repo_exact",
                 match_value="y", match_confidence=1.0),
    ])
    s.commit()
    d = compute_coverage(s)
    assert d == {"status": "ok", "registry_total": 4, "linked_servers": 2,
                 "coverage_pct": 50.0,
                 "newest_advisory_fetched_at": "2026-07-04T12:00:00"}, d
    print("PASS")
