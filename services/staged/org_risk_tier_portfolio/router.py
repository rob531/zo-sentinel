"""HTTP surface for organization risk-tier portfolio reporting."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ApiKey, McpServerRegistry, Org, User

from .logic import get_org_risk_portfolio

router = APIRouter(prefix="/api", tags=["org-risk-tier-portfolio"])


class PortfolioServer(BaseModel):
    server_id: str
    name: str | None = None
    risk_tier: str | None = None
    trust_score: float | None = None
    verdict: str | None = None
    last_assessed: datetime | None = None


class OrgRiskTierPortfolioResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    org_id: str
    org_name: str
    user_count: int
    api_key_count: int
    tier_counts: dict[str, int]
    mean_trust_score: float
    distinct_verdicts: int
    servers: list[PortfolioServer]


@router.get(
    "/orgs/{org_id}/risk-portfolio",
    response_model=OrgRiskTierPortfolioResponse,
)
def org_risk_tier_portfolio(
    org_id: str,
    limit: int = Query(default=100, ge=1),
    db: Session = Depends(get_session),
) -> OrgRiskTierPortfolioResponse:
    portfolio: Any = get_org_risk_portfolio(db, org_id, limit)
    return OrgRiskTierPortfolioResponse.model_validate(portfolio)


def _self_test() -> None:
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    for table in (
        Org.__table__,
        User.__table__,
        ApiKey.__table__,
        McpServerRegistry.__table__,
    ):
        table.create(engine)

    testing_session = sessionmaker(bind=engine, expire_on_commit=False)
    db = testing_session()
    db.add(Org(id="org-1", name="Example Org"))
    db.add_all(
        [
            User(
                id="user-1",
                org_id="org-1",
                email="one@example.com",
                password_hash="hash-1",
            ),
            User(
                id="user-2",
                org_id="org-1",
                email="two@example.com",
                password_hash="hash-2",
            ),
        ]
    )
    db.add_all(
        [
            ApiKey(
                id="key-1",
                org_id="org-1",
                key_hash="key-hash-1",
                label="primary",
            ),
            ApiKey(
                id="key-2",
                org_id="org-1",
                key_hash="key-hash-2",
                label="secondary",
            ),
        ]
    )
    db.add_all(
        [
            McpServerRegistry(
                server_id="server-1",
                name="Server One",
                risk_tier="low",
                trust_score=0.8,
                verdict="approved",
            ),
            McpServerRegistry(
                server_id="server-2",
                name="Server Two",
                risk_tier="low",
                trust_score=0.6,
                verdict="approved",
            ),
            McpServerRegistry(
                server_id="server-3",
                name="Server Three",
                risk_tier="high",
                trust_score=0.4,
                verdict="review",
            ),
        ]
    )
    db.commit()
    db.close()

    that_app = FastAPI()
    that_app.include_router(router)

    def override_get_session():
        session = testing_session()
        try:
            yield session
        finally:
            session.close()

    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/orgs/org-1/risk-portfolio")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert sum(payload["tier_counts"].values()) == 3
        assert payload["api_key_count"] == 2
        assert payload["user_count"] == 2
        assert payload["org_name"] == "Example Org"
        assert len(payload["servers"]) == 3

    print("PASS")


if __name__ == "__main__":
    _self_test()