from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry
from .logic import get_risk_tier_policy


class RiskTierDistribution(BaseModel):
    tier: str
    count: int
    avg_trust: Optional[float] = None
    min_trust: Optional[float] = None
    max_trust: Optional[float] = None


class RiskTierThreshold(BaseModel):
    tier: str
    min_score: float
    max_score: float


class RiskTierPolicyResponse(BaseModel):
    tiers: list[RiskTierDistribution]
    thresholds: list[RiskTierThreshold]
    total_scored: int
    unscored: int


router = APIRouter(prefix="/api")


@router.get("/risk/tier-policy", response_model=RiskTierPolicyResponse)
def risk_tier_policy(
    session: Session = Depends(get_session),
) -> RiskTierPolicyResponse:
    return get_risk_tier_policy(session)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    with TestSession() as test_session:
        test_session.execute(
            text(
                """
                INSERT INTO mcp_server_registry (server_id, risk_tier, trust_score)
                VALUES
                    (:id1, 'TRUSTED_GENERAL', 95.0),
                    (:id2, 'TRUSTED_GENERAL', 90.0),
                    (:id3, 'CAUTION_LIMITED', 55.0),
                    (:id4, NULL, NULL)
                """
            ),
            {"id1": "test-1", "id2": "test-2", "id3": "test-3", "id4": "test-4"},
        )
        test_session.commit()

    def override_get_session():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/risk/tier-policy")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert any(
            tier["tier"] == "TRUSTED_GENERAL" and tier["count"] == 2
            for tier in payload["tiers"]
        )
        assert payload["unscored"] == 1
        assert payload["thresholds"]
        assert all(
            {"tier", "min_score", "max_score"} <= set(threshold)
            for threshold in payload["thresholds"]
        )

    print("PASS")