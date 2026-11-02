"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` and delegates data access to the real application database
through the relative logic import.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore

from .logic import criteria_version_drift

router = APIRouter(prefix="/api", tags=["criteria-version-drift"])


class VersionDriftEntry(BaseModel):
    decision_rule_version: str | None
    rows: int
    last_scored_at: datetime | None


class AxisVersionDrift(BaseModel):
    axis_name: str
    versions: list[VersionDriftEntry]
    version_count: int
    latest_version: str | None
    latest_share: float


class CriteriaVersionDriftResponse(BaseModel):
    days: int
    axes: list[AxisVersionDrift]


@router.get(
    "/scores/criteria-version-drift",
    response_model=CriteriaVersionDriftResponse,
)
def get_criteria_version_drift(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_session),
) -> CriteriaVersionDriftResponse:
    result: Any = criteria_version_drift(db, days)

    if hasattr(result, "model_dump"):
        result = result.model_dump()
    elif hasattr(result, "dict"):
        result = result.dict()

    if not isinstance(result, dict):
        result = {"days": days, "axes": result}

    return CriteriaVersionDriftResponse(
        days=int(result.get("days", days)),
        axes=result.get("axes", []),
    )


def _self_test() -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpLlmAxisScore.__table__.create(engine)
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_get_session():
        with testing_session() as session:
            yield session

    now = datetime.now(timezone.utc)
    with testing_session() as session:
        session.add_all(
            [
                McpLlmAxisScore(
                    id=1,
                    server_id=1,
                    axis_name="test-axis",
                    decision_rule_version="v1",
                    model_version="model-a",
                    scored_at=now - timedelta(days=1),
                ),
                McpLlmAxisScore(
                    id=2,
                    server_id=2,
                    axis_name="test-axis",
                    decision_rule_version="v1",
                    model_version="model-b",
                    scored_at=now - timedelta(days=1, hours=1),
                ),
                McpLlmAxisScore(
                    id=3,
                    server_id=3,
                    axis_name="test-axis",
                    decision_rule_version="v2",
                    model_version="model-c",
                    scored_at=now,
                ),
            ]
        )
        session.commit()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get(
            "/api/scores/criteria-version-drift",
            params={"days": 2},
        )
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["axes"]) == 1
        assert payload["axes"][0]["version_count"] == 2

    print("PASS")


if __name__ == "__main__":
    _self_test()