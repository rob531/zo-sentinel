from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import FreshnessStat

try:
    from .logic import get_sla_breaches as _get_sla_breaches
except ImportError:
    from .logic import get_freshness_sla_breaches as _get_sla_breaches


router = APIRouter(prefix="/api", tags=["freshness"])


class FreshnessSlaBreach(BaseModel):
    surface: str
    computed_at: str
    age_seconds: float
    compute_seconds: float
    payload: Any


class FreshnessSlaSummary(BaseModel):
    surfaces_checked: int
    breaching_count: int
    slowest: List[Dict[str, Any]]


class FreshnessSlaBreachResponse(BaseModel):
    max_age_seconds: int
    checked_at: str
    breaching: List[FreshnessSlaBreach]
    summary: FreshnessSlaSummary


@router.get(
    "/freshness/sla-breaches",
    response_model=FreshnessSlaBreachResponse,
)
def get_freshness_sla_breaches(
    max_age_seconds: int = Query(default=900, ge=0),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    return _get_sla_breaches(session, max_age_seconds=max_age_seconds)


if __name__ == "__main__":
    import json
    from datetime import datetime, timedelta, timezone

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
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    reference_now = datetime.now(timezone.utc).replace(microsecond=0)
    rows = [
        (
            "stale_alpha",
            {"version": 1},
            (reference_now - timedelta(hours=2)).isoformat(),
            4.5,
        ),
        (
            "stale_beta",
            {"version": 2},
            (reference_now - timedelta(minutes=30)).isoformat(),
            2.25,
        ),
        (
            "fresh_gamma",
            {"version": 3},
            (reference_now - timedelta(minutes=5)).isoformat(),
            1.0,
        ),
    ]

    with test_engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE freshness_stats (
                surface TEXT NOT NULL,
                payload TEXT,
                computed_at TIMESTAMP NOT NULL,
                compute_seconds FLOAT NOT NULL
            )
            """
        )
        for surface, payload, computed_at, compute_seconds in rows:
            connection.exec_driver_sql(
                """
                INSERT INTO freshness_stats
                    (surface, payload, computed_at, compute_seconds)
                VALUES (?, ?, ?, ?)
                """,
                (
                    surface,
                    json.dumps(payload),
                    computed_at,
                    compute_seconds,
                ),
            )

    def override_get_session():
        with TestSession() as test_session:
            yield test_session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get(
            "/api/freshness/sla-breaches",
            params={"max_age_seconds": 900},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["max_age_seconds"] == 900
    assert body["summary"]["surfaces_checked"] == 3
    assert body["summary"]["breaching_count"] == 2
    breaching_names = {item["surface"] for item in body["breaching"]}
    assert breaching_names == {"stale_alpha", "stale_beta"}
    assert "fresh_gamma" not in breaching_names
    assert body["summary"]["slowest"]

    print("PASS")