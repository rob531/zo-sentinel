"""vuln_registry_linker.py -- deterministic advisory<->server linkage.

For each advisory, intersect its canonical identities (vuln_identity.
advisory_identities, rebuilt from stored package/ecosystem + repo refs in
source_url) with each server's identities (vuln_identity.server_identities from
url/name/meta). An intersection = a link at confidence 1.0 with match_basis +
match_value recorded (repo_exact | package_exact) -- NEVER a fuzzy match. This
is the join that makes "known-vuln MCPs" a defensible claim.

Bounded + idempotent (unique advisory_id+server_id). Admin route + CLI.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Dict, List, Set

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, VulnAdvisory, VulnLink
from vuln_identity import advisory_identities, repo_key, server_identities
from verdict_breakdown_api import Principal, require_admin

router = APIRouter(prefix="/api", tags=["vuln"])


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _advisory_ids(adv: VulnAdvisory) -> Set[str]:
    # Prefer the identities persisted at ingest (includes repo refs from
    # OSV references); fall back to package identity for older rows.
    if adv.identities:
        return set(adv.identities)
    refs = [adv.source_url] if adv.source_url else []
    return advisory_identities(adv.ecosystem, adv.package, None, refs)


def build_server_index(db: Session) -> Dict[str, List[str]]:
    """identity_key -> [server_id]. One pass over the registry."""
    index: Dict[str, List[str]] = {}
    for sid, url, name, meta in db.execute(
            select(McpServerRegistry.server_id, McpServerRegistry.url,
                   McpServerRegistry.name, McpServerRegistry.meta)):
        m = None
        if meta:
            try:
                m = json.loads(meta)
            except Exception:
                m = None
        for key in server_identities(url, name, m):
            index.setdefault(key, []).append(sid)
    return index


def relink(db: Session, limit_advisories: int = 0) -> dict:
    """Rebuild links for all advisories against the current registry index.
    Idempotent: existing (advisory,server) pairs are skipped."""
    stats = {"advisories": 0, "links_created": 0, "links_existing": 0}
    index = build_server_index(db)
    existing = {(l.advisory_id, l.server_id) for l in
                db.execute(select(VulnLink)).scalars()}
    q = select(VulnAdvisory)
    if limit_advisories:
        q = q.limit(limit_advisories)
    for adv in db.execute(q).scalars():
        stats["advisories"] += 1
        for key in _advisory_ids(adv):
            for sid in index.get(key, []):
                if (adv.id, sid) in existing:
                    stats["links_existing"] += 1
                    continue
                basis = "repo_exact" if key.startswith("repo:") else "package_exact"
                db.add(VulnLink(advisory_id=adv.id, server_id=sid,
                                match_basis=basis, match_value=key,
                                match_confidence=1.0, linked_at=_now()))
                existing.add((adv.id, sid))
                stats["links_created"] += 1
    db.commit()
    return stats


@router.post("/vuln/relink")
def api_relink(db: Session = Depends(get_session),
               principal: Principal = Depends(require_admin)) -> dict:
    return relink(db)


if __name__ == "__main__":
    import os
    os.environ.setdefault("DATABASE_URL", "sqlite://")
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db import Base
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    s.add_all([
        McpServerRegistry(server_id="s1", name="mcp-inspector",
                          url="https://github.com/anthropics/mcp-inspector"),
        McpServerRegistry(server_id="s2", name="unrelated",
                          url="https://github.com/other/thing"),
        VulnAdvisory(id="GHSA-xxxx-1", feed="ghsa", severity="HIGH",
                     ecosystem="npm", package="@mcp/inspector",
                     source_url="https://github.com/anthropics/mcp-inspector"),
    ])
    s.commit()
    st = relink(s)
    assert st["links_created"] == 1, st
    links = list(s.execute(select(VulnLink)).scalars())
    assert len(links) == 1 and links[0].server_id == "s1"
    assert links[0].match_basis == "repo_exact" and links[0].match_confidence == 1.0
    assert relink(s)["links_created"] == 0          # idempotent
    print("PASS")
