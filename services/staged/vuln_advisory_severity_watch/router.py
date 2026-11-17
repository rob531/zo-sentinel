"""HTTP surface for the vulnerability advisory severity-watch service."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_session
from app.models import McpServerRegistry, VulnAdvisory, VulnLink

from .logic import get_severity_watch as _get_severity_watch

router = APIRouter(prefix="/api/vuln/advisories", tags=["vulnerability-advisories"])


class SeverityWatchTotals(BaseModel):
    advisories: int
    linked_servers: int
    links: int


class EcosystemCount(BaseModel):
    ecosystem: str | None = None
    count: int


class AdvisoryExposure(BaseModel):
    advisory_id: int
    summary: str | None = None
    severity: str | None = None
    ecosystem: str | None = None
    package: str | None = None
    published_at: datetime | None = None
    linked_server_count: int
    max_match_confidence: float | None = None


class ExposedServer(BaseModel):
    server_id: str
    server_name: str | None = None
    risk_tier: str | None = None
    advisory_count: int
    max_confidence: float | None = None


class SeverityWatchResponse(BaseModel):
    days: int
    generated_at: datetime
    totals: SeverityWatchTotals
    by_severity: dict[str, int]
    by_ecosystem: list[EcosystemCount]
    top_advisories: list[AdvisoryExposure]
    top_exposed_servers: list[ExposedServer]


@router.get("/severity-watch", response_model=SeverityWatchResponse)
def get_severity_watch(
    ecosystem: str | None = Query(default=None),
    days: int = Query(default=30, ge=1, le=3650),
    db: Session = Depends(get_session),
) -> SeverityWatchResponse:
    result: dict[str, Any] = _get_severity_watch(db, ecosystem=ecosystem, days=days)
    return SeverityWatchResponse(**result)


def _run_self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        engine,
        tables=[
            McpServerRegistry.__table__,
            VulnAdvisory.__table__,
            VulnLink.__table__,
        ],
    )
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = TestingSession()

    now = datetime.now(timezone.utc)
    db.add_all(
        [
            McpServerRegistry(
                server_id="server-one",
                name="Server One",
                risk_tier="high",
            ),
            McpServerRegistry(
                server_id="server-two",
                name="Server Two",
                risk_tier="medium",
            ),
            VulnAdvisory(
                id=1,
                feed="test-feed",
                summary="Critical npm advisory",
                severity="critical",
                ecosystem="npm",
                package="critical-package",
                affected_ranges=[],
                aliases=[],
                source_url="https://example.invalid/advisory/1",
                published_at=now - timedelta(days=1),
                fetched_at=now,
                identities=[],
                content_hash="hash-1",
            ),
            VulnAdvisory(
                id=2,
                feed="test-feed",
                summary="High pip advisory",
                severity="high",
                ecosystem="pip",
                package="high-package",
                affected_ranges=[],
                aliases=[],
                source_url="https://example.invalid/advisory/2",
                published_at=now - timedelta(days=2),
                fetched_at=now,
                identities=[],
                content_hash="hash-2",
            ),
            VulnAdvisory(
                id=3,
                feed="test-feed",
                summary="Low npm advisory one",
                severity="low",
                ecosystem="npm",
                package="low-package-one",
                affected_ranges=[],
                aliases=[],
                source_url="https://example.invalid/advisory/3",
                published_at=now - timedelta(days=3),
                fetched_at=now,
                identities=[],
                content_hash="hash-3",
            ),
            VulnAdvisory(
                id=4,
                feed="test-feed",
                summary="Low npm advisory two",
                severity="low",
                ecosystem="npm",
                package="low-package-two",
                affected_ranges=[],
                aliases=[],
                source_url="https://example.invalid/advisory/4",
                published_at=now - timedelta(days=4),
                fetched_at=now,
                identities=[],
                content_hash="hash-4",
            ),
            VulnLink(
                id=1,
                advisory_id=1,
                server_id="server-one",
                match_basis="package",
                match_value="critical-package",
                match_confidence=0.8,
                linked_at=now,
            ),
            VulnLink(
                id=2,
                advisory_id=2,
                server_id="server-one",
                match_basis="package",
                match_value="high-package",
                match_confidence=0.95,
                linked_at=now,
            ),
            VulnLink(
                id=3,
                advisory_id=3,
                server_id="server-two",
                match_basis="package",
                match_value="low-package-one",
                match_confidence=0.8,
                linked_at=now,
            ),
        ]
    )
    db.commit()

    from fastapi import FastAPI

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = lambda: db

    with TestClient(that_app) as client:
        response = client.get("/api/vuln/advisories/severity-watch?days=30")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["totals"]["advisories"] == 4
        assert payload["by_severity"]["critical"] == 1
        assert payload["by_severity"]["low"] == 2
        assert {"ecosystem": "npm", "count": 3} in payload["by_ecosystem"]
        assert payload["top_exposed_servers"][0]["server_id"] == "server-one"
        assert payload["top_exposed_servers"][0]["advisory_count"] == 2
        assert payload["top_exposed_servers"][0]["max_confidence"] == 0.95

    db.close()
    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _run_self_test()