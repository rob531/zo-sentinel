"""HTTP surface for the tier age distribution service."""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry

from .logic import tier_age_distribution

router = APIRouter(prefix="/api/registry", tags=["registry"])


class TierAgeTotals(BaseModel):
    scored: int
    never_scored: int
    by_tier: dict[str, int]


class TierAgeDistributionResponse(BaseModel):
    buckets: list[str]
    tiers: dict[str, dict[str, int]]
    totals: TierAgeTotals


@router.get("/tier-age", response_model=TierAgeDistributionResponse)
def get_tier_age_distribution(
    db: Session = Depends(get_session),
) -> TierAgeDistributionResponse:
    return TierAgeDistributionResponse.model_validate(tier_age_distribution(db))


if __name__ == "__main__":
    test_app = FastAPI()
    test_app.include_router(router)

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    def override_get_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    test_app.dependency_overrides[get_session] = override_get_session

    now = datetime.utcnow()
    seed_rows = [
        McpServerRegistry(
            server_id="tier-age-now",
            risk_tier="high",
            verdict="malicious",
            trust_score=0.1,
            last_assessed=now,
            last_seen=now,
        ),
        McpServerRegistry(
            server_id="tier-age-three-days",
            risk_tier="medium",
            verdict="suspicious",
            trust_score=0.4,
            last_assessed=now - timedelta(days=3),
            last_seen=now,
        ),
        McpServerRegistry(
            server_id="tier-age-ten-days",
            risk_tier="high",
            verdict="suspicious",
            trust_score=0.3,
            last_assessed=now - timedelta(days=10),
            last_seen=now,
        ),
        McpServerRegistry(
            server_id="tier-age-sixty-days",
            risk_tier="medium",
            verdict="unknown",
            trust_score=0.5,
            last_assessed=now - timedelta(days=60),
            last_seen=now,
        ),
        McpServerRegistry(
            server_id="tier-age-never",
            risk_tier="high",
            verdict="unknown",
            trust_score=None,
            last_assessed=None,
            last_seen=now,
        ),
        McpServerRegistry(
            server_id="tier-age-now-medium",
            risk_tier="medium",
            verdict="safe",
            trust_score=0.9,
            last_assessed=now,
            last_seen=now,
        ),
    ]

    with TestSession() as session:
        session.add_all(seed_rows)
        session.commit()

    from fastapi.testclient import TestClient

    response = TestClient(test_app).get("/api/registry/tier-age")
    assert response.status_code == 200
    payload = response.json()
    assert "never" in payload["buckets"]
    assert payload["tiers"]["high"]["never"] >= 1
    assert payload["tiers"]["high"]["0_1d"] >= 1
    assert payload["tiers"]["medium"]["1_7d"] >= 1
    assert payload["tiers"]["high"]["7_30d"] >= 1
    assert payload["tiers"]["medium"]["30d_plus"] >= 1
    print("PASS")