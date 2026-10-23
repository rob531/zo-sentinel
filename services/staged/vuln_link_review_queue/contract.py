from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import case, func, select, text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, VulnAdvisory, VulnLink


class ReviewLink(BaseModel):
    link_id: int
    server_id: str
    server_name: Optional[str]
    server_risk_tier: Optional[str]
    advisory_id: str
    feed: Optional[str]
    severity: Optional[str]
    ecosystem: Optional[str]
    package: Optional[str]
    match_basis: str
    match_value: str
    match_confidence: float
    linked_at: Optional[str]


class ReviewResponse(BaseModel):
    total: int
    min_confidence: float
    links: list[ReviewLink]


router = APIRouter()


def _as_string(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


@router.get("/api/vuln/links/review", response_model=ReviewResponse)
def review_vuln_links(
    min_confidence: float = Query(default=0.0),
    severity: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    session: Session = Depends(get_session),
) -> ReviewResponse:
    joins = (
        select(
            VulnLink.id.label("link_id"),
            VulnLink.server_id.label("server_id"),
            McpServerRegistry.name.label("server_name"),
            McpServerRegistry.risk_tier.label("server_risk_tier"),
            VulnLink.advisory_id.label("advisory_id"),
            VulnAdvisory.feed.label("feed"),
            VulnAdvisory.severity.label("severity"),
            VulnAdvisory.ecosystem.label("ecosystem"),
            VulnAdvisory.package.label("package"),
            VulnLink.match_basis.label("match_basis"),
            VulnLink.match_value.label("match_value"),
            VulnLink.match_confidence.label("match_confidence"),
            VulnLink.linked_at.label("linked_at"),
        )
        .select_from(VulnLink)
        .outerjoin(
            VulnAdvisory,
            VulnLink.advisory_id == VulnAdvisory.id,
        )
        .outerjoin(
            McpServerRegistry,
            VulnLink.server_id == McpServerRegistry.server_id,
        )
    )

    filters = [VulnLink.match_confidence >= min_confidence]
    if severity is not None:
        filters.append(VulnAdvisory.severity == severity)

    filtered = joins.where(*filters)
    count_query = (
        select(func.count())
        .select_from(VulnLink)
        .outerjoin(
            VulnAdvisory,
            VulnLink.advisory_id == VulnAdvisory.id,
        )
        .outerjoin(
            McpServerRegistry,
            VulnLink.server_id == McpServerRegistry.server_id,
        )
        .where(*filters)
    )

    total = int(session.execute(count_query).scalar_one())
    rows = session.execute(
        filtered.order_by(
            VulnLink.match_confidence.desc(),
            case((VulnLink.linked_at.is_(None), 1), else_=0),
            VulnLink.linked_at.desc(),
        ).limit(limit)
    ).all()

    links = [
        ReviewLink(
            link_id=int(row.link_id),
            server_id=str(row.server_id),
            server_name=_as_string(row.server_name),
            server_risk_tier=_as_string(row.server_risk_tier),
            advisory_id=str(row.advisory_id),
            feed=_as_string(row.feed),
            severity=_as_string(row.severity),
            ecosystem=_as_string(row.ecosystem),
            package=_as_string(row.package),
            match_basis=str(row.match_basis),
            match_value=str(row.match_value),
            match_confidence=float(row.match_confidence),
            linked_at=_as_string(row.linked_at),
        )
        for row in rows
    ]

    return ReviewResponse(
        total=total,
        min_confidence=float(min_confidence),
        links=links,
    )


app = FastAPI()
app.include_router(router)


def _run_self_test() -> None:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    VulnAdvisory.__table__.create(engine)
    McpServerRegistry.__table__.create(engine)
    VulnLink.__table__.create(engine)

    with SessionLocal() as session:
        session.execute(
            text(
                "INSERT INTO vuln_advisories "
                "(id, feed, summary, severity, ecosystem, package, source_url, published_at) "
                "VALUES (:id, :feed, :summary, :severity, :ecosystem, :package, :source_url, :published_at)"
            ),
            [
                {
                    "id": "adv-1",
                    "feed": "osv",
                    "summary": "Example advisory",
                    "severity": "HIGH",
                    "ecosystem": "npm",
                    "package": "example-package",
                    "source_url": "https://example.invalid/advisory/adv-1",
                    "published_at": "2025-01-01T00:00:00",
                },
                {
                    "id": "adv-2",
                    "feed": "github",
                    "summary": "Second advisory",
                    "severity": "MEDIUM",
                    "ecosystem": "pypi",
                    "package": "second-package",
                    "source_url": "https://example.invalid/advisory/adv-2",
                    "published_at": "2025-01-02T00:00:00",
                },
            ],
        )
        session.execute(
            text(
                "INSERT INTO mcp_server_registry "
                "(server_id, name, risk_tier, verdict) "
                "VALUES (:server_id, :name, :risk_tier, :verdict)"
            ),
            [
                {
                    "server_id": "server-1",
                    "name": "Joined Server",
                    "risk_tier": "high",
                    "verdict": "approved",
                },
                {
                    "server_id": "server-2",
                    "name": "Second Server",
                    "risk_tier": "medium",
                    "verdict": "approved",
                },
            ],
        )
        session.execute(
            text(
                "INSERT INTO vuln_links "
                "(id, advisory_id, server_id, match_basis, match_value, match_confidence, linked_at) "
                "VALUES (:id, :advisory_id, :server_id, :match_basis, :match_value, :match_confidence, :linked_at)"
            ),
            [
                {
                    "id": 1,
                    "advisory_id": "adv-1",
                    "server_id": "server-1",
                    "match_basis": "package",
                    "match_value": "example-package",
                    "match_confidence": 0.95,
                    "linked_at": "2025-01-03T00:00:00",
                },
                {
                    "id": 2,
                    "advisory_id": "adv-2",
                    "server_id": "server-2",
                    "match_basis": "package",
                    "match_value": "second-package",
                    "match_confidence": 0.20,
                    "linked_at": "2025-01-04T00:00:00",
                },
                {
                    "id": 3,
                    "advisory_id": "missing-advisory",
                    "server_id": "unmatched-server",
                    "match_basis": "name",
                    "match_value": "unknown",
                    "match_confidence": 0.60,
                    "linked_at": "2025-01-05T00:00:00",
                },
            ],
        )
        session.commit()

    def override_get_session():
        with SessionLocal() as session:
            yield session

    local_app = FastAPI()
    local_app.include_router(router)
    local_app.dependency_overrides[get_session] = override_get_session

    with TestClient(local_app) as client:
        response = client.get("/api/vuln/links/review")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 3
        assert len(payload["links"]) == 3

        filtered = client.get(
            "/api/vuln/links/review",
            params={"min_confidence": 0.8},
        )
        assert filtered.status_code == 200
        filtered_payload = filtered.json()
        assert len(filtered_payload["links"]) == 1
        assert filtered_payload["links"][0]["link_id"] == 1
        assert filtered_payload["links"][0]["server_name"] == "Joined Server"

        unmatched = next(
            link for link in payload["links"] if link["link_id"] == 3
        )
        assert unmatched["server_name"] is None

    print("PASS")


if __name__ == "__main__":
    _run_self_test()