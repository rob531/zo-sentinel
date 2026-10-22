from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry, VulnAdvisory, VulnLink


class AdvisoryResponse(BaseModel):
    id: Any
    feed: Optional[Any] = None
    summary: Optional[Any] = None
    severity: Optional[Any] = None
    ecosystem: Optional[Any] = None
    package: Optional[Any] = None
    affected_ranges: Optional[Any] = None
    aliases: Optional[Any] = None
    source_url: Optional[Any] = None
    published_at: Optional[datetime] = None
    fetched_at: Optional[datetime] = None
    identities: Optional[Any] = None
    content_hash: Optional[Any] = None


class AffectedServerResponse(BaseModel):
    server_id: Any
    name: Optional[Any] = None
    risk_tier: Optional[Any] = None
    trust_score: Optional[float] = None
    match_basis: Optional[Any] = None
    match_value: Optional[Any] = None
    match_confidence: Optional[float] = None
    linked_at: Optional[datetime] = None


class AdvisoryDetailResponse(BaseModel):
    advisory: AdvisoryResponse
    affected_servers: list[AffectedServerResponse]
    affected_server_count: int


router = APIRouter(prefix="/api", tags=["vulnerability advisories"])


def _get_advisory_detail(
    advisory_id: int, session: Session
) -> Optional[AdvisoryDetailResponse]:
    advisory = (
        session.query(VulnAdvisory)
        .filter(VulnAdvisory.id == advisory_id)
        .one_or_none()
    )
    if advisory is None:
        return None

    linked_rows = (
        session.query(VulnLink, McpServerRegistry)
        .join(
            McpServerRegistry,
            McpServerRegistry.server_id == VulnLink.server_id,
        )
        .filter(VulnLink.advisory_id == advisory_id)
        .order_by(VulnLink.match_confidence.desc())
        .all()
    )

    affected_servers = [
        AffectedServerResponse(
            server_id=server.server_id,
            name=server.name,
            risk_tier=server.risk_tier,
            trust_score=server.trust_score,
            match_basis=link.match_basis,
            match_value=link.match_value,
            match_confidence=link.match_confidence,
            linked_at=link.linked_at,
        )
        for link, server in linked_rows
    ]

    return AdvisoryDetailResponse(
        advisory=AdvisoryResponse(
            id=advisory.id,
            feed=advisory.feed,
            summary=advisory.summary,
            severity=advisory.severity,
            ecosystem=advisory.ecosystem,
            package=advisory.package,
            affected_ranges=advisory.affected_ranges,
            aliases=advisory.aliases,
            source_url=advisory.source_url,
            published_at=advisory.published_at,
            fetched_at=advisory.fetched_at,
            identities=advisory.identities,
            content_hash=advisory.content_hash,
        ),
        affected_servers=affected_servers,
        affected_server_count=len(affected_servers),
    )


@router.get(
    "/vuln/advisory/{advisory_id}",
    response_model=AdvisoryDetailResponse,
)
def get_advisory_detail(
    advisory_id: int,
    session: Session = Depends(get_session),
) -> AdvisoryDetailResponse:
    detail = _get_advisory_detail(advisory_id, session)
    if detail is None:
        raise HTTPException(
            status_code=404,
            detail=f"Advisory {advisory_id} was not found",
        )
    return detail


app = FastAPI(title="Vulnerability Advisory Detail API")
app.include_router(router)


def run_self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    McpServerRegistry.__table__.create(engine, checkfirst=True)
    VulnAdvisory.__table__.create(engine, checkfirst=True)
    VulnLink.__table__.create(engine, checkfirst=True)

    now = datetime.now(timezone.utc)
    session = SessionLocal()
    try:
        session.add(
            McpServerRegistry(
                server_id="server-high",
                name="High Confidence Server",
                risk_tier="high",
                trust_score=0.95,
            )
        )
        session.add(
            McpServerRegistry(
                server_id="server-low",
                name="Lower Confidence Server",
                risk_tier="medium",
                trust_score=0.70,
            )
        )
        session.add(
            VulnAdvisory(
                id=101,
                feed="nvd",
                summary="Example advisory",
                severity="high",
                ecosystem="pypi",
                package="example-package",
                affected_ranges=["<2.0.0"],
                aliases=["CVE-2024-0001"],
                source_url="https://example.invalid/advisory/101",
                published_at=now,
                fetched_at=now,
                identities={"cve": ["CVE-2024-0001"]},
                content_hash="example-content-hash",
            )
        )
        session.add(
            VulnLink(
                id=1,
                advisory_id=101,
                server_id="server-high",
                match_basis="package",
                match_value="example-package",
                match_confidence=0.93,
                linked_at=now,
            )
        )
        session.add(
            VulnLink(
                id=2,
                advisory_id=101,
                server_id="server-low",
                match_basis="name",
                match_value="Example Server",
                match_confidence=0.61,
                linked_at=now,
            )
        )
        session.commit()
    finally:
        session.close()

    def override_get_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/vuln/advisory/101")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["affected_server_count"] == 2, payload
        assert payload["affected_servers"][0]["match_basis"] == "package"
        assert payload["affected_servers"][0]["match_confidence"] == 0.93

        missing = client.get("/api/vuln/advisory/999999")
        assert missing.status_code == 404, missing.text
        assert "detail" in missing.json()

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    run_self_test()