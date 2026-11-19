"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` (the attribute generate_spine/include_spine mount). Thin:
injects the real Session via Depends(get_session), delegates to logic.py.
Intra-service imports are RELATIVE (`from .logic import ...`) so the module
works identically at services.staged.<name> and services.active.<name> --
promotion moves the directory, never rewrites an import.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_trust_score_distribution

router = APIRouter(
    prefix="/api",
    tags=["server_trust_score_distribution"],
)


class TrustScoreBucketResponse(BaseModel):
    bucket: str
    count: int
    distinct_sources: int
    tier_mix: dict[str, int]
    verdict_mix: dict[str, int]


class TrustScoreDistributionResponse(BaseModel):
    total_servers: int
    scored_servers: int
    unscored_servers: int
    buckets: list[TrustScoreBucketResponse]
    tier_totals: dict[str, int]
    trust_tier_disagreements: int


@router.get(
    "/registry/trust-score-distribution",
    response_model=TrustScoreDistributionResponse,
)
def get_trust_score_distribution_endpoint(
    db: Session = Depends(get_session),
) -> TrustScoreDistributionResponse:
    result = get_trust_score_distribution(db)
    return TrustScoreDistributionResponse(**result)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import McpServerRegistry

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    testing_session = sessionmaker(bind=engine)()

    testing_session.add_all(
        [
            McpServerRegistry(
                server_id="trust-distribution-test-1",
                name="test-server-1",
                trust_score=10,
                risk_tier="low",
                verdict="allow",
                registry_source="test",
            ),
            McpServerRegistry(
                server_id="trust-distribution-test-2",
                name="test-server-2",
                trust_score=50,
                risk_tier="medium",
                verdict="review",
                registry_source="test",
            ),
            McpServerRegistry(
                server_id="trust-distribution-test-3",
                name="test-server-3",
                trust_score=85,
                risk_tier="high",
                verdict="deny",
                registry_source="test",
            ),
            McpServerRegistry(
                server_id="trust-distribution-test-4",
                name="test-server-4",
                trust_score=None,
                risk_tier="unknown",
                verdict="unknown",
                registry_source="test",
            ),
        ]
    )
    testing_session.commit()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = lambda: testing_session

    response = TestClient(test_app).get("/api/registry/trust-score-distribution")
    assert response.status_code == 200
    payload = response.json()
    assert payload["scored_servers"] == 3
    assert payload["unscored_servers"] == 1
    assert sum(bucket["count"] for bucket in payload["buckets"]) == 3

    print("PASS")