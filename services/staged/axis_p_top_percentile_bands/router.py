"""router.py -- the service's HTTP surface.

Exposes `router` and delegates percentile-band computation to the real data layer.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import percentile_bands

router = APIRouter(prefix="/api/axis", tags=["axis"])


class PercentileBands(BaseModel):
    p10: float
    p50: float
    p90: float


class PercentileBandsResponse(BaseModel):
    axis_name: str
    days: int
    bands: PercentileBands
    above_p90_count: int
    sample_size: int


@router.get(
    "/{axis_name}/percentile-bands",
    response_model=PercentileBandsResponse,
)
def get_percentile_bands(
    axis_name: str,
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_session),
) -> PercentileBandsResponse:
    result = percentile_bands(db, axis_name, days)
    return PercentileBandsResponse(
        axis_name=axis_name,
        days=days,
        bands=result["bands"],
        above_p90_count=result["above_p90_count"],
        sample_size=result["sample_size"],
    )


if __name__ == "__main__":
    from datetime import datetime, timedelta, timezone

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import McpLlmAxisScore

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpLlmAxisScore.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine)
    test_db = TestSession()

    now = datetime.now(timezone.utc)
    for index in range(10):
        test_db.add(
            McpLlmAxisScore(
                id=index + 1,
                server_id=f"server-{index + 1}",
                axis_name="overall_risk",
                model_version="test-model",
                p_top=(index + 1) / 10,
                scored_at=now - timedelta(hours=index),
            )
        )
    test_db.commit()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = lambda: test_db

    with TestClient(test_app) as client:
        response = client.get(
            "/api/axis/overall_risk/percentile-bands",
            params={"days": 30},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["sample_size"] == 10
        assert payload["bands"]["p10"] <= payload["bands"]["p50"]
        assert payload["bands"]["p50"] <= payload["bands"]["p90"]

    test_db.close()
    test_engine.dispose()
    print("PASS")