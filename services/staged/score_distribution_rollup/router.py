from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpLlmAxisScore

from .logic import score_distribution_rollup

router = APIRouter(prefix="/api", tags=["score-distribution"])


class ProbabilityBucket(BaseModel):
    lo: float
    hi: float
    count: int


class ScoreDistributionResponse(BaseModel):
    axis: str
    days: int
    buckets: list[ProbabilityBucket]
    escalated_count: int
    median_p_top: float | None
    total: int


@router.get(
    "/scores/distribution",
    response_model=ScoreDistributionResponse,
)
def get_score_distribution(
    axis: str = Query("overall_risk"),
    days: int = Query(30, ge=1),
    db: Session = Depends(get_session),
) -> ScoreDistributionResponse:
    result: dict[str, Any] = score_distribution_rollup(db, axis=axis, days=days)
    return ScoreDistributionResponse(**result)


if __name__ == "__main__":
    from fastapi.testclient import TestClient

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpLlmAxisScore.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with TestSession() as session:
        session.add_all(
            [
                McpLlmAxisScore(
                    id="rollup-test-1",
                    server_id="server-1",
                    axis_name="overall_risk",
                    model_version="test-model",
                    p_top=0.05,
                    p_critical=0.01,
                    p_danger=0.02,
                    escalated=False,
                    scored_at=now,
                ),
                McpLlmAxisScore(
                    id="rollup-test-2",
                    server_id="server-2",
                    axis_name="overall_risk",
                    model_version="test-model",
                    p_top=0.50,
                    p_critical=0.10,
                    p_danger=0.20,
                    escalated=True,
                    scored_at=now,
                ),
                McpLlmAxisScore(
                    id="rollup-test-3",
                    server_id="server-3",
                    axis_name="overall_risk",
                    model_version="test-model",
                    p_top=0.95,
                    p_critical=0.40,
                    p_danger=0.30,
                    escalated=False,
                    scored_at=now,
                ),
                McpLlmAxisScore(
                    id="rollup-test-4",
                    server_id="server-1",
                    axis_name="security_risk",
                    model_version="test-model",
                    p_top=0.70,
                    p_critical=0.20,
                    p_danger=0.25,
                    escalated=False,
                    scored_at=now,
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get(
            "/api/scores/distribution",
            params={"axis": "overall_risk", "days": 365},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total"] == 3, payload
        assert any(
            bucket["lo"] == 0.9
            and bucket["hi"] == 1.0
            and bucket["count"] == 1
            for bucket in payload["buckets"]
        ), payload
        assert payload["median_p_top"] == 0.5, payload

    print("PASS")