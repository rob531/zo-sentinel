"""logic.py -- advisory severity drilldown data and computation layer."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, VulnAdvisory, VulnLink


def _aliases(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return [value]
    if isinstance(value, list):
        return value
    return [value]


def get_advisory_drilldown(db: Session, advisory_id: str) -> Optional[Dict[str, Any]]:
    """Return an advisory and the registry servers linked to it."""
    advisory = db.execute(
        select(VulnAdvisory).where(VulnAdvisory.id == advisory_id)
    ).scalar_one_or_none()
    if advisory is None:
        return None

    rows = db.execute(
        select(VulnLink, McpServerRegistry)
        .join(
            McpServerRegistry,
            McpServerRegistry.server_id == VulnLink.server_id,
        )
        .where(VulnLink.advisory_id == advisory_id)
    ).all()

    affected_servers = []
    by_tier: Dict[str, int] = {}

    for link, server in rows:
        tier = server.risk_tier or "unknown"
        by_tier[tier] = by_tier.get(tier, 0) + 1
        affected_servers.append(
            {
                "server_id": link.server_id,
                "name": server.name,
                "risk_tier": server.risk_tier,
                "verdict": server.verdict,
                "match_basis": link.match_basis,
                "match_confidence": link.match_confidence,
                "linked_at": link.linked_at,
            }
        )

    return {
        "advisory": {
            "id": advisory.id,
            "feed": advisory.feed,
            "severity": advisory.severity,
            "ecosystem": advisory.ecosystem,
            "package": advisory.package,
            "aliases": _aliases(advisory.aliases),
            "published_at": advisory.published_at,
        },
        "affected_servers": affected_servers,
        "counts": {
            "servers": len(affected_servers),
            "by_tier": by_tier,
        },
    }


def get_drilldown(db: Session, advisory_id: str) -> Optional[Dict[str, Any]]:
    return get_advisory_drilldown(db, advisory_id)


def get_advisory_severity_drilldown(
    db: Session, advisory_id: str
) -> Optional[Dict[str, Any]]:
    return get_advisory_drilldown(db, advisory_id)


if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from fastapi.testclient import TestClient

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    VulnAdvisory.__table__.create(engine)
    VulnLink.__table__.create(engine)

    testing_session = sessionmaker(bind=engine)
    now = datetime.now(timezone.utc)

    with testing_session() as db:
        db.add_all(
            [
                VulnAdvisory(
                    id="advisory-1",
                    feed="test-feed",
                    summary="Test advisory",
                    severity="high",
                    ecosystem="npm",
                    package="example-package",
                    aliases=["example-alias"],
                    source_url="https://example.invalid/advisory-1",
                    published_at=now,
                    fetched_at=now,
                ),
                McpServerRegistry(
                    server_id="server-1",
                    name="Example Server One",
                    risk_tier="high",
                    verdict="blocked",
                    trust_score=80,
                ),
                McpServerRegistry(
                    server_id="server-2",
                    name="Example Server Two",
                    risk_tier="low",
                    verdict="approved",
                    trust_score=95,
                ),
                VulnLink(
                    id=1,
                    advisory_id="advisory-1",
                    server_id="server-1",
                    match_basis="package",
                    match_value="example-package",
                    match_confidence=0.95,
                    linked_at=now,
                ),
                VulnLink(
                    id=2,
                    advisory_id="advisory-1",
                    server_id="server-2",
                    match_basis="alias",
                    match_value="example-alias",
                    match_confidence=0.90,
                    linked_at=now,
                ),
            ]
        )
        db.commit()

    test_app = FastAPI()

    @test_app.get("/api/vuln/advisories/{advisory_id}/drilldown")
    def drilldown_route(
        advisory_id: str, db: Session = Depends(get_session)
    ) -> Dict[str, Any]:
        result = get_advisory_drilldown(db, advisory_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Advisory not found")
        return result

    def override_get_session():
        with testing_session() as db:
            yield db

    test_app.dependency_overrides[get_session] = override_get_session
    with TestClient(test_app) as client:
        response = client.get("/api/vuln/advisories/advisory-1/drilldown")
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["affected_servers"]) == 2
        assert payload["counts"]["servers"] == 2
        assert payload["counts"]["by_tier"] == {"high": 1, "low": 1}

    print("PASS")