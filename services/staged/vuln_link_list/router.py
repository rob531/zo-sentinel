"""HTTP surface for vulnerability link discovery."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, VulnAdvisory, VulnLink

from .logic import get_vuln_links

router = APIRouter(prefix="/api/vuln", tags=["vuln"])


class VulnLinkItem(BaseModel):
    link_id: int
    server_id: str | None = None
    server_name: str | None = None
    server_risk_tier: str | None = None
    advisory_id: int
    severity: str | None = None
    ecosystem: str | None = None
    package: str | None = None
    feed: str | None = None
    summary: str | None = None
    source_url: str | None = None
    match_basis: str
    match_value: str
    match_confidence: float
    linked_at: datetime | None = None
    published_at: datetime | None = None


class VulnLinksResponse(BaseModel):
    count: int
    links: list[VulnLinkItem]


@router.get("/links", response_model=VulnLinksResponse)
def list_vuln_links(
    server_id: str | None = None,
    advisory_id: int | None = None,
    min_confidence: float | None = None,
    db: Session = Depends(get_session),
) -> VulnLinksResponse:
    rows = get_vuln_links(
        db,
        server_id=server_id,
        advisory_id=advisory_id,
        min_confidence=min_confidence,
    )
    links = [VulnLinkItem.model_validate(row) for row in rows]
    return VulnLinksResponse(count=len(links), links=links)


def _self_test() -> None:
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
    McpServerRegistry.__table__.create(engine)
    VulnAdvisory.__table__.create(engine)
    VulnLink.__table__.create(engine)
    TestingSession = sessionmaker(bind=engine, expire_on_commit=False)

    with TestingSession() as db:
        db.add(
            McpServerRegistry(
                server_id="server-1",
                name="Known Server",
                risk_tier="medium",
            )
        )
        db.add(
            VulnAdvisory(
                id=101,
                feed="osv",
                summary="Known advisory",
                severity="high",
                ecosystem="PyPI",
                package="example-package",
                source_url="https://example.test/advisories/101",
            )
        )
        db.add(
            VulnLink(
                advisory_id=101,
                server_id="server-1",
                match_basis="package",
                match_value="example-package",
                match_confidence=0.95,
            )
        )
        db.add(
            VulnLink(
                advisory_id=999,
                server_id="unmatched-server",
                match_basis="manual",
                match_value="unmatched",
                match_confidence=0.25,
            )
        )
        db.commit()

        test_app = FastAPI()
        test_app.include_router(router)
        test_app.dependency_overrides[get_session] = lambda: db

        with TestClient(test_app) as client:
            response = client.get("/api/vuln/links")
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["count"] == 2, payload
            known = next(link for link in payload["links"] if link["advisory_id"] == 101)
            assert known["match_basis"] == "package", known
            assert known["severity"] == "high", known

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _self_test()