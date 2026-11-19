"""HTTP surface for advisory-to-server attribution."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry, VulnAdvisory, VulnLink

from .logic import get_advisory_servers

router = APIRouter(prefix="/api", tags=["advisory-server-attribution"])


class AdvisoryResponse(BaseModel):
    id: str
    feed: str
    severity: Optional[str]
    ecosystem: Optional[str]
    package: Optional[str]
    summary: Optional[str]
    source_url: str
    published_at: Optional[datetime]


class AttributedServerResponse(BaseModel):
    server_id: str
    name: Optional[str]
    risk_tier: Optional[str]
    trust_score: Optional[float]
    verdict: Optional[str]
    match_basis: str
    match_value: str
    match_confidence: float
    linked_at: datetime


class AdvisoryServersResponse(BaseModel):
    advisory: AdvisoryResponse
    servers: list[AttributedServerResponse]
    server_count: int


@router.get(
    "/advisories/{advisory_id}/servers",
    response_model=AdvisoryServersResponse,
)
def advisory_servers(
    advisory_id: str,
    db: Session = Depends(get_session),
) -> AdvisoryServersResponse:
    return get_advisory_servers(advisory_id=advisory_id, db=db)


def _self_test() -> None:
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
        db.add_all(
            [
                McpServerRegistry(
                    server_id="server-alpha",
                    name="Alpha Server",
                    risk_tier="high",
                    trust_score=0.91,
                    verdict="vulnerable",
                ),
                McpServerRegistry(
                    server_id="server-beta",
                    name="Beta Server",
                    risk_tier="medium",
                    trust_score=0.74,
                    verdict="review",
                ),
                VulnAdvisory(
                    id="ADV-1001",
                    feed="security-advisories",
                    summary="Test advisory",
                    severity="HIGH",
                    ecosystem="pypi",
                    package="example-package",
                    affected_ranges=[{"introduced": "1.0.0"}],
                    source_url="https://example.test/advisories/ADV-1001",
                    published_at=datetime(2025, 1, 1),
                ),
                VulnLink(
                    advisory_id="ADV-1001",
                    server_id="server-alpha",
                    match_basis="package",
                    match_value="example-package",
                    match_confidence=0.98,
                ),
                VulnLink(
                    advisory_id="ADV-1001",
                    server_id="server-beta",
                    match_basis="dependency",
                    match_value="example-package",
                    match_confidence=0.85,
                ),
            ]
        )
        db.commit()

        test_app = FastAPI()
        test_app.include_router(router)
        test_app.dependency_overrides[get_session] = lambda: db

        from fastapi.testclient import TestClient

        response = TestClient(test_app).get("/api/advisories/ADV-1001/servers")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["server_count"] == 2
        assert payload["advisory"]["severity"] == "HIGH"
        assert {server["server_id"] for server in payload["servers"]} == {
            "server-alpha",
            "server-beta",
        }

    print("PASS")


if __name__ == "__main__":
    _self_test()