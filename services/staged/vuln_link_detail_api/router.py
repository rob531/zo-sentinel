from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, VulnAdvisory, VulnLink
from .logic import get_vuln_links

router = APIRouter(prefix="/api", tags=["vulnerability"])


class VulnLinkResponse(BaseModel):
    link_id: int
    advisory_id: str
    server_id: str
    server_name: Optional[str] = None
    risk_tier: Optional[str] = None
    match_basis: Optional[str] = None
    match_value: Optional[str] = None
    match_confidence: Optional[float] = None
    severity: Optional[str] = None
    ecosystem: Optional[str] = None
    package: Optional[str] = None
    feed: Optional[str] = None
    summary: Optional[str] = None
    source_url: Optional[str] = None
    published_at: Optional[datetime] = None
    linked_at: Optional[datetime] = None


class VulnLinksResponse(BaseModel):
    count: int
    links: list[VulnLinkResponse]


@router.get("/vuln/links", response_model=VulnLinksResponse)
def list_vuln_links(
    advisory_id: Optional[str] = Query(default=None),
    server_id: Optional[str] = Query(default=None),
    severity: Optional[str] = Query(default=None),
    min_confidence: Optional[float] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    result = get_vuln_links(
        session=session,
        advisory_id=advisory_id,
        server_id=server_id,
        severity=severity,
        min_confidence=min_confidence,
        limit=limit,
        offset=offset,
    )

    if isinstance(result, dict):
        links = result.get("links", [])
        count = result.get("count", len(links))
    else:
        links = result
        count = len(links)

    return {"count": int(count), "links": links}


if __name__ == "__main__":
    from datetime import datetime, timezone

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, insert
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
    test_session = TestSession()

    now = datetime.now(timezone.utc)
    test_session.execute(
        insert(McpServerRegistry.__table__),
        [
            {
                "server_id": "server-a",
                "name": "Server A",
                "risk_tier": "high",
                "confidence": 0.9,
                "description": "Test server A",
                "first_seen": now,
                "last_assessed": now,
                "last_scanned": now,
                "last_seen": now,
                "meta": {},
                "registry_source": "test",
                "scan_count": 1,
                "trust_score": 0.8,
                "url": "http://server-a",
                "verdict": "active",
                "verdict_reasoning": "Test fixture",
            },
            {
                "server_id": "server-b",
                "name": "Server B",
                "risk_tier": "medium",
                "confidence": 0.8,
                "description": "Test server B",
                "first_seen": now,
                "last_assessed": now,
                "last_scanned": now,
                "last_seen": now,
                "meta": {},
                "registry_source": "test",
                "scan_count": 1,
                "trust_score": 0.7,
                "url": "http://server-b",
                "verdict": "active",
                "verdict_reasoning": "Test fixture",
            },
        ],
    )
    test_session.execute(
        insert(VulnAdvisory.__table__),
        [
            {
                "id": 1,
                "feed": "test-feed",
                "summary": "Test advisory one",
                "severity": "high",
                "ecosystem": "npm",
                "package": "package-one",
                "source_url": "http://advisories/1",
                "published_at": now,
                "affected_ranges": [],
                "aliases": [],
                "content_hash": "hash-one",
                "fetched_at": now,
                "identities": [],
            },
            {
                "id": 2,
                "feed": "test-feed",
                "summary": "Test advisory two",
                "severity": "medium",
                "ecosystem": "pypi",
                "package": "package-two",
                "source_url": "http://advisories/2",
                "published_at": now,
                "affected_ranges": [],
                "aliases": [],
                "content_hash": "hash-two",
                "fetched_at": now,
                "identities": [],
            },
        ],
    )
    test_session.execute(
        insert(VulnLink.__table__),
        [
            {
                "id": 1,
                "advisory_id": 1,
                "server_id": "server-a",
                "match_basis": "package",
                "match_value": "package-one",
                "match_confidence": 0.95,
                "linked_at": now,
            },
            {
                "id": 2,
                "advisory_id": 2,
                "server_id": "server-b",
                "match_basis": "package",
                "match_value": "package-two",
                "match_confidence": 0.65,
                "linked_at": now,
            },
            {
                "id": 3,
                "advisory_id": 1,
                "server_id": "server-b",
                "match_basis": "identity",
                "match_value": "package-one",
                "match_confidence": 0.55,
                "linked_at": now,
            },
        ],
    )
    test_session.commit()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        yield test_session

    test_app.dependency_overrides[get_session] = override_get_session
    client = TestClient(test_app)

    response = client.get("/api/vuln/links")
    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 3
    assert len(payload["links"]) == 3
    assert all("advisory_id" in link for link in payload["links"])
    assert all("severity" in link for link in payload["links"])
    assert all("server_name" in link for link in payload["links"])

    filtered = client.get("/api/vuln/links", params={"min_confidence": 0.9})
    assert filtered.status_code == 200
    filtered_payload = filtered.json()
    assert filtered_payload["count"] == 1
    assert len(filtered_payload["links"]) == 1
    assert filtered_payload["links"][0]["match_confidence"] >= 0.9

    print("PASS")