"""Orphaned vulnerability links: vuln_links rows whose server_id is not in the registry.

A link is deterministic (exact package/repo identity), so a link that points at a
server the registry does not hold means the registry dropped or renamed the server
after linking. Grouped per orphan server_id, worst first.
"""
from __future__ import annotations

from typing import Any, Dict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import McpServerRegistry, VulnAdvisory, VulnLink


def get_orphan_links(db: Session, *, limit: int = 100, min_confidence: float = 0.0) -> Dict[str, Any]:
    rows = db.execute(
        select(VulnLink.server_id, VulnLink.advisory_id, VulnLink.match_confidence,
               VulnLink.linked_at, VulnAdvisory.severity)
        .select_from(VulnLink)
        .outerjoin(McpServerRegistry, VulnLink.server_id == McpServerRegistry.server_id)
        .outerjoin(VulnAdvisory, VulnLink.advisory_id == VulnAdvisory.id)
        .where(McpServerRegistry.server_id.is_(None))
        .where(VulnLink.match_confidence >= min_confidence)
    ).all()

    by_server: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        g = by_server.setdefault(r.server_id, {"server_id": r.server_id, "link_count": 0,
                                               "_adv": set(), "max_match_confidence": 0.0,
                                               "_sev": set(), "latest_linked_at": None})
        g["link_count"] += 1
        g["_adv"].add(r.advisory_id)
        g["max_match_confidence"] = max(g["max_match_confidence"], float(r.match_confidence or 0.0))
        if r.severity:
            g["_sev"].add(r.severity)
        if r.linked_at and (g["latest_linked_at"] is None or r.linked_at > g["latest_linked_at"]):
            g["latest_linked_at"] = r.linked_at

    orphans = []
    for g in by_server.values():
        orphans.append({"server_id": g["server_id"], "link_count": g["link_count"],
                        "advisory_count": len(g["_adv"]),
                        "max_match_confidence": g["max_match_confidence"],
                        "severities": sorted(g["_sev"]),
                        "latest_linked_at": g["latest_linked_at"]})
    orphans.sort(key=lambda o: (-o["link_count"], -o["max_match_confidence"], o["server_id"]))
    return {"count": len(orphans), "orphans": orphans[:limit]}
