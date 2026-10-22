from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, VulnAdvisory, VulnLink


class AffectedServer(BaseModel):
    server_id: str
    name: Optional[str] = None
    risk_tier: Optional[str] = None
    trust_score: Optional[float] = None
    match_basis: Optional[str] = None
    match_value: Optional[str] = None
    match_confidence: Optional[float] = None
    linked_at: Optional[datetime] = None


class Advisory(BaseModel):
    id: Any
    feed: Optional[str] = None
    summary: Optional[str] = None
    severity: Optional[str] = None
    ecosystem: Optional[str] = None
    package: Optional[str] = None
    affected_ranges: Any = None
    aliases: Any = None
    source_url: Optional[str] = None
    published_at: Optional[datetime] = None
    fetched_at: Optional[datetime] = None
    identities: Any = None
    content_hash: Optional[str] = None


class AdvisoryDetailResponse(BaseModel):
    advisory: Advisory
    affected_servers: list[AffectedServer]
    affected_server_count: int


router = APIRouter(prefix="/api/vuln", tags=["vulnerability-advisories"])


def get_advisory_detail(
    session: Session, advisory_id: Any
) -> Optional[dict[str, Any]]:
    """Return an advisory and its linked servers, or None when not found."""
    advisory = session.execute(
        select(VulnAdvisory).where(VulnAdvisory.id == advisory_id)
    ).scalar_one_or_none()

    if advisory is None:
        return None

    rows = session.execute(
        select(VulnLink, McpServerRegistry)
        .join(
            McpServerRegistry,
            McpServerRegistry.server_id == VulnLink.server_id,
        )
        .where(VulnLink.advisory_id == advisory_id)
        .order_by(VulnLink.match_confidence.desc())
    ).all()

    affected_servers = [
        {
            "server_id": server.server_id,
            "name": server.name,
            "risk_tier": server.risk_tier,
            "trust_score": server.trust_score,
            "match_basis": link.match_basis,
            "match_value": link.match_value,
            "match_confidence": link.match_confidence,
            "linked_at": link.linked_at,
        }
        for link, server in rows
    ]

    advisory_data = {
        "id": advisory.id,
        "feed": advisory.feed,
        "summary": advisory.summary,
        "severity": advisory.severity,
        "ecosystem": advisory.ecosystem,
        "package": advisory.package,
        "affected_ranges": advisory.affected_ranges,
        "aliases": advisory.aliases,
        "source_url": advisory.source_url,
        "published_at": advisory.published_at,
        "fetched_at": advisory.fetched_at,
        "identities": advisory.identities,
        "content_hash": advisory.content_hash,
    }

    return {
        "advisory": advisory_data,
        "affected_servers": affected_servers,
        "affected_server_count": len(affected_servers),
    }


# Compatibility aliases for callers using the service-specific naming.
get_vuln_advisory_detail = get_advisory_detail
get_advisory_by_id = get_advisory_detail
fetch_advisory_detail = get_advisory_detail


@router.get(
    "/advisory/{advisory_id}",
    response_model=AdvisoryDetailResponse,
    summary="Get a vulnerability advisory and its affected MCP servers",
)
def advisory_detail(
    advisory_id: int,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    result = get_advisory_detail(session, advisory_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Advisory not found")
    return result


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

    VulnAdvisory.__table__.create(engine, checkfirst=True)
    McpServerRegistry.__table__.create(engine, checkfirst=True)
    VulnLink.__table__.create(engine, checkfirst=True)

    with SessionLocal() as session:
        session.execute(
            VulnAdvisory.__table__.insert().values(
                id=1,
                feed="osv",
                summary="Example advisory",
                severity="HIGH",
                ecosystem="PyPI",
                package="example-package",
                affected_ranges=["<1.2.3"],
                aliases=["CVE-2024-0001"],
                source_url="https://example.invalid/CVE-2024-0001",
                published_at=datetime(2024, 1, 1),
                fetched_at=datetime(2024, 1, 2),
                identities=["example-package"],
                content_hash="example-hash",
            )
        )
        session.execute(
            McpServerRegistry.__table__.insert().values(
                server_id="server-a",
                name="Example server A",
                risk_tier="HIGH",
                trust_score=0.91,
            )
        )
        session.execute(
            McpServerRegistry.__table__.insert().values(
                server_id="server-b",
                name="Example server B",
                risk_tier="LOW",
                trust_score=0.72,
            )
        )
        session.execute(
            VulnLink.__table__.insert().values(
                id=1,
                advisory_id=1,
                server_id="server-a",
                match_basis="package",
                match_value="example-package",
                match_confidence=0.98,
                linked_at=datetime(2024, 1, 2),
            )
        )
        session.execute(
            VulnLink.__table__.insert().values(
                id=2,
                advisory_id=1,
                server_id="server-b",
                match_basis="name",
                match_value="Example server B",
                match_confidence=0.75,
                linked_at=datetime(2024, 1, 3),
            )
        )
        session.commit()

    def override_get_session():
        with SessionLocal() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    client = TestClient(test_app)
    response = client.get("/api/vuln/advisory/1")
    assert response.status_code == 200
    body = response.json()
    assert body["affected_server_count"] == 2
    assert body["affected_servers"][0]["match_basis"] == "package"
    assert body["affected_servers"][0]["match_confidence"] == 0.98

    missing = client.get("/api/vuln/advisory/999999")
    assert missing.status_code == 404
    assert missing.json()

    print("PASS")