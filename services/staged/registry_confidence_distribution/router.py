"""HTTP surface for the registry confidence distribution service."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry

from .logic import get_confidence_distribution

router = APIRouter(prefix="/api/registry", tags=["registry"])


class ConfidenceDistributionResponse(BaseModel):
    total_servers: int
    confidence_histogram: dict[str, int]
    trust_score_histogram: dict[str, int]
    by_registry_source: dict[str, int]
    by_risk_tier: dict[str, int]
    assessed_count: int
    unassessed_count: int


@router.get(
    "/confidence-distribution",
    response_model=ConfidenceDistributionResponse,
)
def confidence_distribution(
    db: Session = Depends(get_session),
) -> ConfidenceDistributionResponse:
    return ConfidenceDistributionResponse(**get_confidence_distribution(db))


def _run_self_test() -> None:
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(test_engine)
    session_factory = sessionmaker(bind=test_engine, expire_on_commit=False)

    with session_factory() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="confidence-low",
                    name="confidence-low",
                    registry_source="seeded",
                    verdict="trusted",
                    confidence=0.05,
                    trust_score=5,
                    risk_tier="low",
                    last_assessed=datetime.now(timezone.utc),
                    last_scanned=datetime.now(timezone.utc),
                    scan_count=1,
                ),
                McpServerRegistry(
                    server_id="confidence-high",
                    name="confidence-high",
                    registry_source="seeded",
                    verdict="review",
                    confidence=0.85,
                    trust_score=85,
                    risk_tier="high",
                    last_assessed=datetime.now(timezone.utc),
                    last_scanned=datetime.now(timezone.utc),
                    scan_count=1,
                ),
                McpServerRegistry(
                    server_id="confidence-unknown",
                    name="confidence-unknown",
                    registry_source="seeded",
                    verdict="unknown",
                    confidence=None,
                    trust_score=None,
                    risk_tier="medium",
                    last_assessed=datetime.now(timezone.utc),
                    last_scanned=datetime.now(timezone.utc),
                    scan_count=1,
                ),
                McpServerRegistry(
                    server_id="never-assessed",
                    name="never-assessed",
                    registry_source="seeded",
                    verdict="pending",
                    confidence=None,
                    trust_score=None,
                    risk_tier="medium",
                    last_assessed=None,
                    last_scanned=None,
                    scan_count=0,
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with session_factory() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    from fastapi.testclient import TestClient

    with TestClient(test_app) as client:
        response = client.get("/api/registry/confidence-distribution")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total_servers"] == 4
        assert sum(payload["confidence_histogram"].values()) == 4
        assert payload["unassessed_count"] == 1
        assert "seeded" in payload["by_registry_source"]

    print("PASS")


if __name__ == "__main__":
    _run_self_test()