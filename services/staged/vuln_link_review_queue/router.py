from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, VulnAdvisory, VulnLink

try:
    from .logic import get_review_queue
except ImportError:  # Allows direct execution of this file for its self-test.
    from logic import get_review_queue  # type: ignore[no-redef]


router = APIRouter()


class ReviewLink(BaseModel):
    link_id: int
    server_id: str
    server_name: str | None
    server_risk_tier: str | None
    advisory_id: str
    feed: str | None
    severity: str | None
    ecosystem: str | None
    package: str | None
    match_basis: str
    match_value: str
    match_confidence: float
    linked_at: str | None


class ReviewQueueResponse(BaseModel):
    total: int
    min_confidence: float
    links: list[ReviewLink]


@router.get(
    "/api/vuln/links/review",
    response_model=ReviewQueueResponse,
    summary="List CVE-to-MCP-server links awaiting human review",
)
def review_links(
    min_confidence: float = Query(default=0.0, ge=0.0),
    severity: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return get_review_queue(
        session=session,
        min_confidence=min_confidence,
        severity=severity,
        limit=limit,
    )


def _run_self_test() -> None:
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    VulnAdvisory.__table__.create(engine)
    McpServerRegistry.__table__.create(engine)
    VulnLink.__table__.create(engine)

    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with TestingSession() as session:
        session.add_all(
            [
                VulnAdvisory(
                    id="adv-high",
                    feed="osv",
                    summary="High-confidence advisory",
                    severity="HIGH",
                    ecosystem="pypi",
                    package="example-high",
                    source_url="https://example.invalid/high",
                    published_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
                ),
                VulnAdvisory(
                    id="adv-low",
                    feed="osv",
                    summary="Low-confidence advisory",
                    severity="MEDIUM",
                    ecosystem="npm",
                    package="example-low",
                    source_url="https://example.invalid/low",
                    published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                ),
                McpServerRegistry(
                    server_id="server-joined",
                    name="Joined Server",
                    risk_tier="high",
                    verdict="review",
                ),
                McpServerRegistry(
                    server_id="server-second",
                    name="Second Server",
                    risk_tier="medium",
                    verdict="allow",
                ),
                VulnLink(
                    id=1,
                    advisory_id="adv-high",
                    server_id="server-joined",
                    match_basis="package_name",
                    match_value="example-high",
                    match_confidence=0.95,
                    linked_at=datetime(2026, 1, 3, tzinfo=timezone.utc),
                ),
                VulnLink(
                    id=2,
                    advisory_id="adv-low",
                    server_id="server-unmatched",
                    match_basis="description",
                    match_value="example-low",
                    match_confidence=0.20,
                    linked_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
                ),
                VulnLink(
                    id=3,
                    advisory_id="adv-missing",
                    server_id="server-second",
                    match_basis="cpe",
                    match_value="cpe:2.3:a:example:missing:*:*:*:*:*:*:*:*",
                    match_confidence=0.50,
                    linked_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                ),
            ]
        )
        session.commit()

    def override_get_session() -> Any:
        with TestingSession() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/vuln/links/review")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert len(payload["links"]) == 3, payload

        filtered = client.get(
            "/api/vuln/links/review",
            params={"min_confidence": 0.8},
        )
        assert filtered.status_code == 200, filtered.text
        filtered_payload = filtered.json()
        assert len(filtered_payload["links"]) == 1, filtered_payload
        assert filtered_payload["links"][0]["match_confidence"] == 0.95

        assert any(link["server_name"] == "Joined Server" for link in payload["links"])
        assert any(link["server_name"] is None for link in payload["links"])

    print("PASS")


if __name__ == "__main__":
    _run_self_test()