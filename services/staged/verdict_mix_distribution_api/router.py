from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

from .logic import get_verdict_mix_distribution


router = APIRouter(prefix="/api", tags=["verdict-mix"])


class RiskTierCount(BaseModel):
    tier: str
    count: int


class VerdictCount(BaseModel):
    verdict: str
    count: int
    avg_trust_score: Optional[float]


class VerdictMixResponse(BaseModel):
    total: int
    by_risk_tier: List[RiskTierCount]
    by_verdict: List[VerdictCount]
    assessed_24h: int


@router.get("/verdict-mix", response_model=VerdictMixResponse)
def get_verdict_mix(
    session: Session = Depends(get_session),
) -> VerdictMixResponse:
    return VerdictMixResponse(**get_verdict_mix_distribution(session))


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    with TestSession() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="trusted-server",
                    risk_tier="trusted",
                    verdict="approved",
                    trust_score=90.0,
                    last_assessed=datetime.now(timezone.utc),
                ),
                McpServerRegistry(
                    server_id="elevated-server",
                    risk_tier="elevated",
                    verdict="restricted",
                    trust_score=40.0,
                    last_assessed=datetime.now(timezone.utc),
                ),
                McpServerRegistry(
                    server_id="critical-server",
                    risk_tier="critical",
                    verdict="unscored",
                    trust_score=None,
                    last_assessed=datetime.now(timezone.utc),
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session
    test_app.include_router(router)

    response = TestClient(test_app).get("/api/verdict-mix")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total"] == 3
    assert {"tier": "critical", "count": 1} in payload["by_risk_tier"]
    unscored = next(
        item for item in payload["by_verdict"] if item["verdict"] == "unscored"
    )
    assert unscored["count"] == 1
    assert unscored["avg_trust_score"] is None

    test_engine.dispose()
    print("PASS")