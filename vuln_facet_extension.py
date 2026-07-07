"""vuln_facet_extension.py -- vuln facets + per-server threat-intel refs.

P2 of docs/DESIGN_NEXT_BUILD_TARGETS_2026_07.md, built on the shipped spine
(vuln_osv_ingestor / vuln_registry_linker / otx_threat_refs). Two additions:

1. extend_facets(db, facets): two boolean facets for the Perspectives
   enumerator --
     has_known_cve              server has >=1 exact vuln_link
     referenced_in_threat_intel server maps to >=1 NON-aggregator
                                threat_intel_ref (via its linked CVE aliases
                                or its hosting domain)
   Kill-switch aware: policy vuln.enabled off => contributes NOTHING (the
   facet universe simply doesn't gain the keys; no counts, no claims).

2. GET /api/servers/{id}/threat_refs -- the per-server read surface the
   threat-intel view consumes. Mirrors vuln_exposure_api's honest-degrade
   semantics: kill-switch off => status=disabled, zero claims. Refs split
   curated vs is_aggregator, provenance verbatim (pulse id/name/created +
   source_url + fetched_at).

THE LINE: OTX refs are CONTEXT, never linkage -- matching here reuses only
deterministic keys (linked CVE aliases, exact hosting domain).
Exemplars: facet_enum_service.py, vuln_exposure_api.py.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Set

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import (McpServerRegistry, ThreatIntelRef, VulnAdvisory,
                        VulnLink)
from verdict_breakdown_api import Principal, get_principal

router = APIRouter(prefix="/api", tags=["vuln"])

_GIT_HOSTS = ("github.com", "gitlab.com", "bitbucket.org")


def kill_switch_on() -> bool:
    try:
        from zo_sentinel import policy
        return policy.flag("vuln.enabled")
    except Exception:
        return False   # fail-closed: unknown state => surface is OFF


def _host_of(url: Optional[str]) -> Optional[str]:
    """Exact hosting domain of a registry url; git forges excluded (same rule
    as otx_threat_refs.hosting_domains -- forge domains aren't the server's
    own hosting surface)."""
    u = (url or "").lower()
    h = re.sub(r"^https?://(www\.)?", "", u).split("/")[0].split(":")[0]
    if (not h or h in _GIT_HOSTS
            or not re.match(r"^[a-z0-9.-]+\.[a-z]{2,}$", h)):
        return None
    return h


def server_cves(db: Session, server_id: str) -> Set[str]:
    """CVE aliases of the server's exactly-linked advisories -- deterministic
    keys only (THE LINE: context matching never invents linkage)."""
    adv_ids = [l for (l,) in db.execute(
        select(VulnLink.advisory_id).where(VulnLink.server_id == server_id))]
    cves: Set[str] = set()
    if not adv_ids:
        return cves
    for adv in db.execute(
            select(VulnAdvisory).where(VulnAdvisory.id.in_(adv_ids))).scalars():
        aliases = adv.aliases if isinstance(adv.aliases, list) else []
        for a in list(aliases) + [adv.id]:
            if isinstance(a, str) and a.upper().startswith("CVE-"):
                cves.add(a.upper())
    return cves


def _ti_indicator_values(db: Session, curated_only: bool) -> Set[str]:
    q = select(ThreatIntelRef.indicator_value)
    if curated_only:
        q = q.where(ThreatIntelRef.is_aggregator.is_(False))
    return {v.upper() for (v,) in db.execute(q) if v}


def _servers_with_ti_ref(db: Session) -> Set[str]:
    """server_ids with >=1 NON-aggregator threat_intel_ref, via linked CVE
    aliases or hosting domain. Bounded: iterates the (small) vuln_links set
    and only domain-scans when a domain indicator exists at all."""
    curated = _ti_indicator_values(db, curated_only=True)
    if not curated:
        return set()
    hit: Set[str] = set()
    linked_servers = {s for (s,) in db.execute(select(VulnLink.server_id).distinct())}
    for sid in linked_servers:
        if server_cves(db, sid) & curated:
            hit.add(sid)
    ti_domains = {v.lower() for v in curated if not v.startswith("CVE-")}
    if ti_domains:
        for srv in db.execute(select(McpServerRegistry)).scalars():
            h = _host_of(srv.url)
            if h and h in ti_domains:
                hit.add(srv.server_id)
    return hit


def extend_facets(db: Session, facets: Dict[str, List[dict]]) -> None:
    """Add the two boolean vuln facets IN PLACE. No-op when the kill-switch
    is off -- an off switch must leave zero trace in the facet universe."""
    if not kill_switch_on():
        return
    total = db.execute(
        select(func.count()).select_from(McpServerRegistry)).scalar() or 0
    linked = db.execute(
        select(func.count(func.distinct(VulnLink.server_id)))).scalar() or 0
    facets["has_known_cve"] = [
        {"value": "true", "count": int(linked)},
        {"value": "false", "count": max(0, total - int(linked))}]
    ti = len(_servers_with_ti_ref(db))
    facets["referenced_in_threat_intel"] = [
        {"value": "true", "count": ti},
        {"value": "false", "count": max(0, total - ti)}]


def server_threat_refs(db: Session, server_id: str) -> dict:
    """{status, server_id, refs:[...]} -- curated first, aggregators after,
    provenance verbatim. disabled => the caller renders INSUFFICIENT."""
    if not kill_switch_on():
        return {"status": "disabled", "server_id": server_id, "refs": []}
    keys = {c.upper() for c in server_cves(db, server_id)}
    srv = db.get(McpServerRegistry, server_id)
    h = _host_of(srv.url) if srv else None
    if h:
        keys.add(h.upper())
    refs: List[dict] = []
    if keys:
        for t in db.execute(
                select(ThreatIntelRef)
                .where(func.upper(ThreatIntelRef.indicator_value).in_(keys))
                .limit(200)).scalars():
            refs.append({
                "indicator_type": t.indicator_type,
                "indicator_value": t.indicator_value,
                "pulse_id": t.pulse_id, "pulse_name": t.pulse_name,
                "pulse_created": t.pulse_created.isoformat() if t.pulse_created else None,
                "is_aggregator": bool(t.is_aggregator),
                "source": t.source, "source_url": t.source_url,
                "fetched_at": t.fetched_at.isoformat() if t.fetched_at else None,
            })
    refs.sort(key=lambda r: (r["is_aggregator"], r["indicator_value"], r["pulse_id"]))
    return {"status": "ok", "server_id": server_id, "count": len(refs),
            "refs": refs}


@router.get("/servers/{server_id}/threat_refs")
def get_server_threat_refs(server_id: str, db: Session = Depends(get_session),
                           principal: Principal = Depends(get_principal)) -> dict:
    return server_threat_refs(db, server_id)


if __name__ == "__main__":
    import os
    os.environ["ZO_VULN_ENABLED"] = "1"
    os.environ.setdefault("DATABASE_URL", "sqlite://")
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db import Base
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    s.add_all([
        McpServerRegistry(server_id="s1", url="https://github.com/o/r"),
        McpServerRegistry(server_id="s2", url="https://mcp.example.io/x"),
        McpServerRegistry(server_id="s3", url="https://github.com/o/clean"),
        VulnAdvisory(id="GHSA-1", feed="ghsa", severity="HIGH", summary="rce",
                     source_url="https://github.com/advisories/GHSA-1",
                     aliases=["CVE-2025-1111"]),
        VulnLink(advisory_id="GHSA-1", server_id="s1", match_basis="repo_exact",
                 match_value="repo:github.com/o/r", match_confidence=1.0),
        # curated pulse on s1's CVE alias + aggregator noise on the same key
        ThreatIntelRef(indicator_type="cve", indicator_value="CVE-2025-1111",
                       pulse_id="p1", pulse_name="curated report",
                       is_aggregator=False, source="otx",
                       source_url="https://otx.alienvault.com/pulse/p1"),
        ThreatIntelRef(indicator_type="cve", indicator_value="CVE-2025-1111",
                       pulse_id="p2", pulse_name="daily cve roundup",
                       is_aggregator=True, source="otx",
                       source_url="https://otx.alienvault.com/pulse/p2"),
        # curated pulse on s2's hosting domain
        ThreatIntelRef(indicator_type="domain", indicator_value="mcp.example.io",
                       pulse_id="p3", pulse_name="malicious hosting",
                       is_aggregator=False, source="otx",
                       source_url="https://otx.alienvault.com/pulse/p3"),
    ])
    s.commit()
    facets: Dict[str, List[dict]] = {}
    extend_facets(s, facets)
    assert facets["has_known_cve"] == [{"value": "true", "count": 1},
                                       {"value": "false", "count": 2}]
    assert facets["referenced_in_threat_intel"] == [
        {"value": "true", "count": 2}, {"value": "false", "count": 1}]
    r = server_threat_refs(s, "s1")
    assert r["status"] == "ok" and r["count"] == 2
    assert r["refs"][0]["is_aggregator"] is False          # curated first
    assert all(x["source_url"].startswith("https://") for x in r["refs"])
    assert server_threat_refs(s, "s2")["count"] == 1        # via domain
    assert server_threat_refs(s, "s3")["count"] == 0        # clean server
    # kill-switch off => no facets, no claims
    os.environ["ZO_VULN_ENABLED"] = "0"
    f2: Dict[str, List[dict]] = {}
    extend_facets(s, f2)
    assert f2 == {} and server_threat_refs(s, "s1")["status"] == "disabled"
    print("PASS")
