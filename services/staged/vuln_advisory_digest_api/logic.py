"""Logic for vulnerability advisory digest APIs over the real application models."""
from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, VulnAdvisory, VulnLink


def _as_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            return [value] if value else []
        if isinstance(decoded, list):
            return decoded
        if decoded is None:
            return []
        return [decoded]
    return [value]


def _iso8601(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _text(value: Any) -> Optional[str]:
    if value is None:
        return None
    return str(value)


def _link_payload(link: VulnLink) -> Dict[str, Any]:
    confidence = link.match_confidence
    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        confidence = 0.0
    return {
        "server_id": _text(link.server_id) or "",
        "match_basis": _text(link.match_basis) or "",
        "match_value": _text(link.match_value) or "",
        "match_confidence": confidence,
    }


def _advisory_payload(
    advisory: VulnAdvisory,
    linked_servers: int,
    links: List[VulnLink],
) -> Dict[str, Any]:
    return {
        "id": _text(advisory.id) or "",
        "feed": _text(advisory.feed) or "",
        "summary": _text(advisory.summary) or "",
        "severity": _text(advisory.severity) or "",
        "ecosystem": _text(advisory.ecosystem),
        "package": _text(advisory.package),
        "affected_ranges": _as_list(advisory.affected_ranges),
        "aliases": _as_list(advisory.aliases),
        "source_url": _text(advisory.source_url),
        "published_at": _iso8601(advisory.published_at),
        "fetched_at": _iso8601(advisory.fetched_at),
        "linked_servers": int(linked_servers),
        "links": [_link_payload(link) for link in links],
    }


def _filters(severity: Optional[str], ecosystem: Optional[str]) -> list:
    filters = []
    if severity is not None:
        filters.append(VulnAdvisory.severity == severity)
    if ecosystem is not None:
        filters.append(VulnAdvisory.ecosystem == ecosystem)
    return filters


def _links_for_advisories(db: Session, advisory_ids: List[Any]) -> Dict[Any, List[VulnLink]]:
    if not advisory_ids:
        return {}
    rows = db.execute(
        select(VulnLink)
        .where(VulnLink.advisory_id.in_(advisory_ids))
        .order_by(VulnLink.linked_at, VulnLink.id)
    ).scalars().all()
    grouped: Dict[Any, List[VulnLink]] = {advisory_id: [] for advisory_id in advisory_ids}
    for link in rows:
        grouped.setdefault(link.advisory_id, []).append(link)
    return grouped


def get_advisories(
    db: Session,
    severity: Optional[str] = None,
    ecosystem: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> Dict[str, Any]:
    """Return filtered vulnerability advisories and their linked-server counts."""
    filters = _filters(severity, ecosystem)
    linked_count = (
        select(func.count(VulnLink.id))
        .where(VulnLink.advisory_id == VulnAdvisory.id)
        .scalar_subquery()
        .label("linked_servers")
    )
    statement = select(VulnAdvisory, linked_count)
    if filters:
        statement = statement.where(*filters)
    statement = statement.order_by(VulnAdvisory.published_at.desc(), VulnAdvisory.id)
    statement = statement.offset(max(0, int(offset))).limit(max(0, int(limit)))

    rows = db.execute(statement).all()
    advisories = [row[0] for row in rows]
    links_by_advisory = _links_for_advisories(db, [advisory.id for advisory in advisories])

    total_statement = select(func.count()).select_from(VulnAdvisory)
    if filters:
        total_statement = total_statement.where(*filters)
    total = int(db.execute(total_statement).scalar() or 0)

    payload = []
    for advisory, count in rows:
        payload.append(
            _advisory_payload(
                advisory,
                int(count or 0),
                links_by_advisory.get(advisory.id, []),
            )
        )
    return {"count": len(payload), "total": total, "advisories": payload}


def get_advisory(db: Session, advisory_id: Any) -> Optional[Dict[str, Any]]:
    """Return one advisory and its linked server details, or None when absent."""
    advisory = db.get(VulnAdvisory, advisory_id)
    if advisory is None:
        return None

    rows = db.execute(
        select(
            VulnLink,
            McpServerRegistry.name,
            McpServerRegistry.risk_tier,
        )
        .outerjoin(
            McpServerRegistry,
            McpServerRegistry.server_id == VulnLink.server_id,
        )
        .where(VulnLink.advisory_id == advisory.id)
        .order_by(VulnLink.linked_at, VulnLink.id)
    ).all()

    links = [row[0] for row in rows]
    advisory_payload = _advisory_payload(advisory, len(links), links)

    servers: List[Dict[str, Any]] = []
    seen = set()
    for link, name, risk_tier in rows:
        server_id = _text(link.server_id) or ""
        if server_id in seen:
            continue
        seen.add(server_id)
        servers.append(
            {
                "server_id": server_id,
                "name": _text(name),
                "risk_tier": _text(risk_tier),
            }
        )

    return {"advisory": advisory_payload, "servers": servers}


def list_advisories(
    db: Session,
    severity: Optional[str] = None,
    ecosystem: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> Dict[str, Any]:
    return get_advisories(db, severity, ecosystem, limit, offset)


def get_vuln_advisories(
    db: Session,
    severity: Optional[str] = None,
    ecosystem: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> Dict[str, Any]:
    return get_advisories(db, severity, ecosystem, limit, offset)


def get_advisory_detail(db: Session, advisory_id: Any) -> Optional[Dict[str, Any]]:
    return get_advisory(db, advisory_id)


def get_vuln_advisory(db: Session, advisory_id: Any) -> Optional[Dict[str, Any]]:
    return get_advisory(db, advisory_id)


if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db import Base

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[VulnAdvisory.__table__, VulnLink.__table__, McpServerRegistry.__table__],
    )
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        db.add_all(
            [
                VulnAdvisory(
                    id="adv-high",
                    feed="osv",
                    summary="high advisory",
                    severity="high",
                    ecosystem="npm",
                    package="example",
                    affected_ranges=["<1.2.3"],
                    aliases=["CVE-TEST"],
                    source_url="https://example.invalid/high",
                ),
                VulnAdvisory(
                    id="adv-low",
                    feed="osv",
                    summary="low advisory",
                    severity="low",
                    ecosystem="npm",
                    package="other",
                    affected_ranges=[],
                    aliases=[],
                    source_url="https://example.invalid/low",
                ),
                VulnAdvisory(
                    id="adv-medium",
                    feed="osv",
                    summary="medium advisory",
                    severity="medium",
                    ecosystem="pypi",
                    package="third",
                    affected_ranges=[],
                    aliases=[],
                    source_url="https://example.invalid/medium",
                ),
                VulnLink(
                    id=1,
                    advisory_id="adv-high",
                    server_id="server-1",
                    match_basis="package",
                    match_value="example",
                    match_confidence=0.95,
                ),
                VulnLink(
                    id=2,
                    advisory_id="adv-high",
                    server_id="server-2",
                    match_basis="alias",
                    match_value="CVE-TEST",
                    match_confidence=0.85,
                ),
            ]
        )
        db.commit()

        result = get_advisories(db, severity="high", ecosystem="npm", limit=50, offset=0)
        assert result["total"] == 1
        assert result["count"] == 1
        assert result["advisories"][0]["id"] == "adv-high"
        assert result["advisories"][0]["linked_servers"] == 2

        detail = get_advisory(db, "adv-high")
        assert detail is not None
        assert len(detail["servers"]) == 2
        assert get_advisory(db, "missing") is None
    finally:
        db.close()
        engine.dispose()

    print("PASS")