"""router.py -- HTTP surface for verdict coverage grouped by registry source.

Exposes `router`, injects the real Session via Depends(get_session), and
delegates aggregation to logic.py.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_verdict_coverage_by_source

router = APIRouter(prefix="/api", tags=["verdict_coverage_by_source"])


class SourceCoverage(BaseModel):
    registry_source: str
    servers: int
    with_verdict: int
    with_risk_tier: int
    with_confidence: int
    scored_ratio: float
    last_assessed_at: datetime | None


class VerdictCoverageBySourceResponse(BaseModel):
    generated_at: datetime
    total_servers: int
    sources: list[SourceCoverage]


@router.get(
    "/verdict-coverage/by-source",
    response_model=VerdictCoverageBySourceResponse,
)
def get_verdict_coverage_by_source_route(
    db: Session = Depends(get_session),
) -> VerdictCoverageBySourceResponse:
    result: dict[str, Any] = get_verdict_coverage_by_source(db)
    return VerdictCoverageBySourceResponse(**result)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import McpServerRegistry

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(bind=test_engine)
    TestingSession = sessionmaker(bind=test_engine, autoflush=False, expire_on_commit=False)

    with TestingSession() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="self-test-1",
                    name="Self test server one",
                    registry_source="source-a",
                    verdict="trusted",
                    risk_tier="low",
                    confidence=0.9,
                    last_assessed=datetime(2024, 1, 1),
                ),
                McpServerRegistry(
                    server_id="self-test-2",
                    name="Self test server two",
                    registry_source="source-a",
                    verdict=None,
                    risk_tier=None,
                    confidence=None,
                    last_assessed=None,
                ),
                McpServerRegistry(
                    server_id="self-test-3",
                    name="Self test server three",
                    registry_source="source-b",
                    verdict="needs_review",
                    risk_tier="medium",
                    confidence=0.7,
                    last_assessed=datetime(2024, 1, 2),
                ),
                McpServerRegistry(
                    server_id="self-test-4",
                    name="Self test server four",
                    registry_source="source-b",
                    verdict=None,
                    risk_tier="low",
                    confidence=0.5,
                    last_assessed=datetime(2024, 1, 3),
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with TestingSession() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/verdict-coverage/by-source")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["sources"]) == 2
    source_a = next(
        source for source in payload["sources"] if source["registry_source"] == "source-a"
    )
    assert source_a["servers"] == 2
    assert source_a["scored_ratio"] == 0.5
    print("PASS")