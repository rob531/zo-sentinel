"""Logic layer for advisory link graph queries over the real application models."""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, VulnAdvisory, VulnLink


def get_advisory_links(db: Session, advisory_id: Any) -> Optional[Dict[str, Any]]:
    """Return an advisory and its linked server registry entries."""
    advisory = db.execute(
        select(VulnAdvisory).where(VulnAdvisory.id == advisory_id)
    ).scalar_one_or_none()

    if advisory is None:
        return None

    rows = db.execute(
        select(
            VulnLink,
            McpServerRegistry.name,
            McpServerRegistry.verdict,
            McpServerRegistry.risk_tier,
        )
        .join(
            McpServerRegistry,
            McpServerRegistry.server_id == VulnLink.server_id,
            isouter=True,
        )
        .where(VulnLink.advisory_id == advisory_id)
        .order_by(VulnLink.id)
    ).all()

    links = [
        {
            "server_id": link.server_id,
            "server_name": server_name,
            "verdict": verdict,
            "risk_tier": risk_tier,
            "match_basis": link.match_basis,
            "match_value": link.match_value,
            "match_confidence": link.match_confidence,
            "linked_at": link.linked_at,
        }
        for link, server_name, verdict, risk_tier in rows
    ]

    return {
        "advisory": {
            "id": advisory.id,
            "summary": advisory.summary,
            "severity": advisory.severity,
            "ecosystem": advisory.ecosystem,
            "package": advisory.package,
            "source_url": advisory.source_url,
            "published_at": advisory.published_at,
        },
        "links": links,
        "total_links": len(links),
    }


def get_advisory_link_graph(db: Session, advisory_id: Any) -> Optional[Dict[str, Any]]:
    """Compatibility alias for callers using the graph terminology."""
    return get_advisory_links(db, advisory_id)


if __name__ == "__main__":
    from datetime import datetime, timezone

    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(test_engine)
    VulnAdvisory.__table__.create(test_engine)
    VulnLink.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    test_db = TestSession()
    test_db.add_all(
        [
            McpServerRegistry(
                server_id="server-known",
                name="Known Server",
                verdict="safe",
                risk_tier="low",
            ),
            McpServerRegistry(
                server_id="server-other",
                name="Other Server",
                verdict="review",
                risk_tier="medium",
            ),
            VulnAdvisory(
                id="advisory-1",
                feed="test-feed",
                source_url="https://example.test/advisory-1",
                summary="Test advisory",
                severity="high",
                ecosystem="npm",
                package="example-package",
                published_at=datetime.now(timezone.utc),
            ),
            VulnLink(
                id=1,
                advisory_id="advisory-1",
                server_id="server-known",
                match_basis="package",
                match_value="example-package",
                match_confidence=0.99,
                linked_at=datetime.now(timezone.utc),
            ),
            VulnLink(
                id=2,
                advisory_id="advisory-1",
                server_id="server-other",
                match_basis="name",
                match_value="example",
                match_confidence=0.75,
                linked_at=datetime.now(timezone.utc),
            ),
        ]
    )
    test_db.commit()

    def override_get_session():
        yield test_db

    that_app = FastAPI()

    @that_app.get("/api/advisories/{advisory_id}/links")
    def read_links(advisory_id: str, db: Session = Depends(get_session)):
        result = get_advisory_links(db, advisory_id)
        if result is None:
            raise HTTPException(status_code=404)
        return result

    that_app.dependency_overrides[get_session] = override_get_session
    response = TestClient(that_app).get("/api/advisories/advisory-1/links")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_links"] == 2
    assert any(link["server_id"] == "server-known" for link in payload["links"])
    print("PASS")
    test_db.close()
    test_engine.dispose()